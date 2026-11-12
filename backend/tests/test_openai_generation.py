"""Task 4: synthetic fixtures and mocked SDK only; never make network requests."""
from protection_fixture import isolate_protection
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx2
from pydantic import ValidationError

from app import config, main
from app.llm_client import LLMClient, OpenAIRequirementsGenerator, RequirementsOutput, create_llm_client
from app.masking import Masker, SanitizedText
from app.openai_client import APITimeoutError, LLMError, LLMTimeoutError, OpenAIClient
from app.orchestrator import Orchestrator, PreviewReviewRequired
from app.jira_adapter import JiraAdapter
from app.session_store import SessionStore, SessionUnavailableError
from app.template_config import get_active_template
from app.validation import Validator
from test_sessions import FakeClock, reference


def fields():
    return dict(summary="Add export", business_context="", requirements="Export a CSV",
                acceptance_criteria="A CSV is downloaded", open_questions="")


def response(value=None, status="completed", output=None):
    return SimpleNamespace(status=status, output=output or [],
                           output_parsed=RequirementsOutput(**fields()) if value is None else value)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        isolate_protection(self)

    def test_selection_and_configuration(self):
        with patch.object(config, "LLM_PROVIDER", "stub"), patch.object(config, "OPENAI_API_KEY", "synthetic-key"):
            self.assertIsInstance(create_llm_client(), LLMClient)
        with patch.object(config, "LLM_PROVIDER", "openai"), patch.object(config, "OPENAI_API_KEY", "synthetic-key"):
            self.assertIsInstance(create_llm_client(), OpenAIRequirementsGenerator)
        for provider, key in [("invalid-synthetic", "synthetic-key"), ("openai", "  ")]:
            with patch.object(config, "LLM_PROVIDER", provider), patch.object(config, "OPENAI_API_KEY", key):
                with self.assertRaises(ValueError) as error:
                    create_llm_client()
                self.assertNotIn("synthetic", str(error.exception))

    def test_default_provider_is_stub_without_environment_or_dotenv(self):
        # Execute only configuration source, replacing dotenv and environment access.
        from pathlib import Path
        import sys
        fake_os = SimpleNamespace(getenv=lambda name, default: default)
        with patch.dict(sys.modules, {"os": fake_os, "dotenv": SimpleNamespace(load_dotenv=lambda: None)}):
            namespace = {}
            exec(Path(config.__file__).read_text(), namespace)
        self.assertEqual(namespace["LLM_PROVIDER"], "stub")
        self.assertEqual(namespace["OPENAI_MODEL"], "gpt-5.4-mini-2026-03-17")

    def test_invalid_config_prevents_application_initialization(self):
        from pathlib import Path
        for provider, key in [("invalid", ""), ("openai", "")]:
            with patch.object(config, "LLM_PROVIDER", provider), patch.object(config, "OPENAI_API_KEY", key):
                with self.assertRaises(ValueError):
                    exec(compile(Path(main.__file__).read_text(), main.__file__, "exec"),
                         {"__name__": "synthetic_startup"})

    def test_strict_schema(self):
        self.assertEqual(list(RequirementsOutput(**fields()).model_dump()), list(fields()))
        self.assertEqual(RequirementsOutput(**dict.fromkeys(fields(), "")).summary, "")
        invalid = [{**fields(), "title": "Synthetic"}, {**fields(), "template": {}},
                   {**fields(), "missing_fields": []}, {**fields(), "summary": 1},
                   {key: value for key, value in fields().items() if key != "requirements"}]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                RequirementsOutput.model_validate(payload)


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        isolate_protection(self)
        self.client = OpenAIClient()
        self.client._client = Mock()
        self.client._client.responses.parse.return_value = response()

    def ask(self):
        return self.client.ask(SanitizedText("Static rules"), SanitizedText("Export a CSV"), RequirementsOutput)

    def test_valid_output_and_sdk_configuration(self):
        self.assertEqual(self.ask().model_dump(), fields())
        sdk = Mock()
        sdk.responses.parse.return_value = response()
        self.client._client = None
        with patch("app.openai_client.OpenAI", return_value=sdk) as constructor, patch.object(config, "OPENAI_API_KEY", "synthetic-key"):
            self.ask()
        constructor.assert_called_once_with(api_key="synthetic-key", timeout=60, max_retries=0)
        self.assertFalse(sdk.responses.parse.call_args.kwargs["store"])
        self.assertIs(sdk.responses.parse.call_args.kwargs["text_format"], RequirementsOutput)

    def test_missing_refused_incomplete_and_invalid_outputs(self):
        missing = response(); missing.output_parsed = None
        refusal = response(output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal")])])
        invalid = response(value=SimpleNamespace(model_dump=lambda: {**fields(), "summary": 42}))
        for result in [missing, refusal, response(status="incomplete"), invalid]:
            self.client._client.responses.parse.return_value = result
            with self.subTest(result=result), self.assertRaises(LLMError) as error:
                self.ask()
            self.assertIsNone(error.exception.__context__)

    def test_provider_and_initialization_errors_are_safe(self):
        for failure in [RuntimeError("token=synthetic-private"), APITimeoutError(request=Mock())]:
            self.client._client.responses.parse.side_effect = failure
            expected = LLMTimeoutError if isinstance(failure, APITimeoutError) else LLMError
            with self.assertRaises(expected) as error:
                self.ask()
            self.assertNotIn("synthetic-private", str(error.exception))
            self.assertIsNone(error.exception.__context__)
        self.client._client = None
        with patch("app.openai_client.OpenAI", side_effect=RuntimeError("token=synthetic-private")):
            with self.assertRaises(LLMError) as error:
                self.ask()
        self.assertIsNone(error.exception.__context__)


class GenerationTests(unittest.TestCase):
    def setUp(self):
        isolate_protection(self)
        self.clock = FakeClock()
        self.store = SessionStore(clock=self.clock)
        self.generator = OpenAIRequirementsGenerator()
        self.sdk = Mock()
        self.sdk.responses.parse.return_value = response()
        self.generator._client._client = self.sdk
        self.orchestrator = Orchestrator(Masker(), self.store, self.generator, Validator(), JiraAdapter())
        self.sid, self.preview = self.orchestrator.mask_preview("Export a CSV password=synthetic-input")

    def test_sanitized_input_and_canonical_output(self):
        result = self.orchestrator.generate_draft(self.sid, self.preview)
        self.assertEqual(result.draft, fields())
        self.assertEqual(result.template, reference())
        self.assertEqual(result.missing_fields, ["business_context"])
        call = self.sdk.responses.parse.call_args.kwargs
        self.assertEqual(call["input"][1]["content"], self.preview)
        self.assertNotIn("synthetic-input", repr(call))
        self.assertNotIn(self.sid, repr(call))

    def test_preview_confirmation_and_output_sanitization(self):
        with self.assertRaises(PreviewReviewRequired) as review:
            self.orchestrator.generate_draft(self.sid, self.preview + " token=synthetic-new")
        self.sdk.responses.parse.assert_not_called()
        self.sdk.responses.parse.return_value = response(value=RequirementsOutput(**{
            key: "password=synthetic-output" for key in fields()
        }))
        generated = self.orchestrator.generate_draft(self.sid, review.exception.masked_text)
        self.assertNotIn("synthetic-output", repr(generated))

    def test_failure_rolls_back_and_retry_then_submission(self):
        deadline = self.store._sessions[self.sid].expires_at
        self.clock.set(10)
        self.sdk.responses.parse.side_effect = APITimeoutError(request=Mock())
        with patch.object(LLMClient, "generate_draft", side_effect=AssertionError("No fallback")):
            with self.assertRaises(LLMTimeoutError):
                self.orchestrator.generate_draft(self.sid, self.preview)
        entry = self.store._sessions[self.sid]
        self.assertEqual(entry.expires_at, deadline)
        self.assertEqual(entry.admitted, 0)
        self.assertTrue(entry.lock.acquire(blocking=False))
        entry.lock.release()
        self.sdk.responses.parse.side_effect = None
        self.sdk.responses.parse.return_value = response(value=RequirementsOutput(**{**fields(), "business_context": "Enable reporting"}))
        generated = self.orchestrator.generate_draft(self.sid, self.preview)
        self.orchestrator.create_ticket(self.sid, generated.draft, generated.template)
        with self.assertRaises(SessionUnavailableError):
            self.orchestrator.create_ticket(self.sid, generated.draft, generated.template)

    def test_failed_generation_can_expire(self):
        self.sdk.responses.parse.side_effect = RuntimeError("synthetic")
        with self.assertRaises(LLMError):
            self.orchestrator.generate_draft(self.sid, self.preview)
        self.clock.set(900)
        with self.assertRaises(SessionUnavailableError):
            self.orchestrator.generate_draft(self.sid, self.preview)
        self.assertEqual(self.sdk.responses.parse.call_count, 1)


class ErrorAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        isolate_protection(self)

    async def test_safe_error_codes(self):
        generator = OpenAIRequirementsGenerator()
        generator._client._client = Mock()
        store = SessionStore()
        orchestrator = Orchestrator(Masker(), store, generator, Validator(), JiraAdapter())
        sid, preview = orchestrator.mask_preview("Synthetic context")
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), base_url="http://test") as client:
            with patch.object(main, "orchestrator", orchestrator):
                for failure, status, code in [(RuntimeError("password=synthetic-private"), 502, "LLM_GENERATION_FAILED"),
                                              (APITimeoutError(request=Mock()), 504, "LLM_TIMEOUT")]:
                    generator._client._client.responses.parse.side_effect = failure
                    result = await client.post("/api/generate-draft", json={"session_id": sid, "masked_text": preview})
                    self.assertEqual(result.status_code, status)
                    self.assertEqual(result.json()["detail"]["code"], code)
                    self.assertNotIn("synthetic-private", result.text)
                    self.assertEqual(store._sessions[sid].admitted, 0)
