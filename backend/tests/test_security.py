"""Synthetic-only security regression tests. No credentials or external calls."""
import importlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx2
from openai import OpenAIError
from pydantic import BaseModel

from app.jira_adapter import JiraAdapter
from app.llm_client import LLMClient
from app.main import app
from app.masking import Masker, PLACEHOLDER, SanitizedText
from app.masking_config import MASK_RULES
from app.orchestrator import Orchestrator, PreviewReviewRequired
from app.session_store import SessionStore, SessionUnavailableError
from app.template_config import get_template
from app.validation import Validator
from app.schemas import TemplateReference


class SyntheticResponse(BaseModel):
    value: str = "safe"


def reference():
    return TemplateReference(mode="requirements", version=1)


def make_orchestrator(client=None):
    return Orchestrator(Masker(), SessionStore(), client or LLMClient(), Validator(), JiraAdapter())


def complete_draft():
    return {field.key: "Synthetic requirement" for field in get_template("requirements").fields}


class MaskingTests(unittest.TestCase):
    def setUp(self):
        self.masker = Masker()

    def test_supported_password_assignments(self):
        for text, secret in [
            ("password=demo-password", "demo-password"),
            ('"password": "demo password with spaces"', "demo password with spaces"),
            ("api_key='synthetic key value'", "synthetic key value"),
            ('password="demo\\\" quoted tail"', 'demo\\\" quoted tail'),
            ("password='demo\\' quoted tail'", "demo\\' quoted tail"),
            ("пароль: synthetic-pass", "synthetic-pass"),
            ("p\u200bassword\u00a0=demo-password", "demo-password"),
        ]:
            with self.subTest(text=text):
                masked, context = self.masker.mask(text, set())
                self.assertNotIn(secret, masked)
                self.assertNotIn(secret, repr(context))

    def test_supported_token_families(self):
        for secret in ["eyJsynthetic.payload.signature", "sk-" + "a" * 20,
                       "ghp_" + "b" * 24, "xoxb-" + "c" * 16, "AKIA" + "A" * 16]:
            with self.subTest(secret=secret):
                masked, context = self.masker.mask(secret, set())
                self.assertNotIn(secret, masked)
                self.assertNotIn(secret, repr(context))

    def test_authorization_formats(self):
        for text in ['Authorization: Basic ZGVtbzpwYXNz', 'Authorization: Bearer demo123',
                     '"Authorization": "Bearer demo123"', 'Proxy-Authorization: Basic ZGVtbzpwYXNz',
                     'Bearer synthetic-token-123']:
            with self.subTest(text=text):
                masked, _ = self.masker.mask(text, set())
                self.assertTrue(PLACEHOLDER.search(masked))
                self.assertNotIn(text.split()[-1].strip('"'), masked)

    def test_repeated_detected_secret_across_fields(self):
        result = self.masker.sanitize_fields(
            {"a": "password=demo-repeated", "b": "also demo-repeated"}, self.masker.context(set()))
        self.assertNotIn("demo-repeated", repr(result))
        self.assertEqual(PLACEHOLDER.search(result["a"]).group(), PLACEHOLDER.search(result["b"]).group())

    def test_unrecognized_unlabeled_values_are_out_of_scope(self):
        text = "An arbitrary unlabeled value demo-unrecognized"
        self.assertEqual(self.masker.mask(text, set())[0], text)

    def test_no_cross_request_secret_memory(self):
        _, context = self.masker.mask("password=demo-unrecognized", set())
        self.assertEqual(self.masker.sanitize_fields({"a": "demo-unrecognized"}, context)["a"], "demo-unrecognized")
        self.assertNotIn("demo-unrecognized", repr(context))

    def test_locked_rules_with_empty_selection(self):
        masked, context = self.masker.mask("password=demo-pass person@example.test", set())
        self.assertNotIn("demo-pass", masked)
        self.assertIn("person@example.test", masked)
        self.assertEqual(context.enabled_keys, frozenset({"secrets", "tokens"}))

    def test_all_optional_categories(self):
        fixtures = {
            "slack_names": "@synthetic.person", "links": "https://example.test/path",
            "emails": "person@example.test", "hashes": "f" * 64,
            "wallets": "A1" * 16, "exchange_ids": "for exchange ABC123456789",
            "ips": "10.20.30.40", "phones": "+7 999 123 45 67",
        }
        self.assertEqual(set(fixtures), {rule.key for rule in MASK_RULES if not rule.locked})
        for key, text in fixtures.items():
            with self.subTest(key=key):
                masked, context = self.masker.mask(text, {key})
                self.assertNotEqual(masked, text)
                self.assertIn(key, context.issued.values())
                self.assertEqual(self.masker.mask(text, set())[0], text)

    def test_personal_values_masked_by_default_and_not_retained(self):
        text = "@demo.person person@example.test +7 999 123 45 67"
        masked, context = self.masker.mask(text)
        for value in ["demo.person", "person@example.test", "+7 999 123 45 67"]:
            self.assertNotIn(value, masked)
            self.assertNotIn(value, repr(context))

    def test_slack_learned_names_preserved(self):
        masked, _ = self.masker.mask("Demo Person  [12:30]\nDemo Person replied", {"slack_names"})
        self.assertNotIn("Demo Person", masked)

    def test_sanitized_placeholder_followed_by_punctuation_is_stable(self):
        masker = Masker()
        context = masker.context(set())
        safe = masker.sanitize_fields({'answer': 'password=synthetic-cross-value',
            'question': 'What about synthetic-cross-value?'}, context)
        self.assertNotIn('synthetic-cross-value', str(safe))
        self.assertEqual(masker.sanitize_fields(safe, context), safe)

    def test_literal_old_placeholders_inert(self):
        masked, context = self.masker.mask("literal [SECRET_1] password=demo-pass", set())
        self.assertIn("[SECRET_1]", masked)
        self.assertNotIn("demo-pass", masked)
        self.assertEqual(self.masker.restore(masked, context, set()), masked)

    def test_forced_literal_collision_retries(self):
        one = "00000000-0000-0000-0000-000000000001"
        two = "00000000-0000-0000-0000-000000000002"
        literal = f"[SECRET:{one}]"
        with patch("app.masking.uuid.uuid4", side_effect=[one, two]):
            masked, context = self.masker.mask(literal + " password=demo-pass", set())
        self.assertIn(literal, masked)
        self.assertNotIn(literal, context.issued)
        self.assertIn(f"[SECRET:{two}]", context.issued)

    def test_multiple_passes_are_idempotent(self):
        masked, context = self.masker.mask("password=demo-pass @demo https://example.test person@example.test")
        for _ in range(3):
            self.assertEqual(self.masker.sanitize_fields({"a": masked}, context)["a"], masked)
        # Also safe when no session context is available.
        self.assertEqual(self.masker.mask(masked)[0], masked)

    def test_quoted_secret_containing_literal_placeholder(self):
        literal = "[SECRET:00000000-0000-0000-0000-000000000001]"
        secret = "demo prefix " + literal + " demo tail"
        masked, context = self.masker.mask('password="' + secret + '"', set())
        self.assertNotIn("demo prefix", masked)
        self.assertNotIn("demo tail", masked)
        self.assertNotIn(secret, repr(context))
        self.assertEqual(self.masker.mask(masked)[0], masked)

    def test_short_secret_does_not_corrupt_placeholder_ids(self):
        masked, context = self.masker.mask("person@example.test", {"emails"})
        result = self.masker.sanitize_fields({"a": masked + " password=a"}, context)["a"]
        self.assertIn(masked, result)
        self.assertNotIn("password=a", result)

    def test_new_secret_on_later_pass(self):
        masked, context = self.masker.mask("password=demo-first", set())
        result = self.masker.sanitize_fields({"a": masked + " token=demo-second"}, context)["a"]
        self.assertIn(masked, result)
        self.assertNotIn("demo-second", result)

    def test_overlapping_url_and_secret_not_recoverable(self):
        masked, context = self.masker.mask("https://example.test/?token=demo-pass&next=ok")
        self.assertNotIn("demo-pass", masked)
        self.assertNotIn("demo-pass", repr(context))
        self.assertEqual(self.masker.mask(masked)[0], masked)

    def test_restore_denies_every_category_and_unknown_ids(self):
        for text in ["password=demo-pass", "person@example.test", "for exchange ABC123456789"]:
            masked, context = self.masker.mask(text)
            for token in [*context.issued, "unknown", "[SECRET_1]"]:
                with self.subTest(token=token):
                    with self.assertRaises(ValueError):
                        self.masker.restore(masked, context, {token})

    def test_ordinary_text_preserved(self):
        text = "Add a settings button; preserve approved requirements."
        self.assertEqual(self.masker.mask(text)[0], text)

    def test_model_payload_sanitizes_all_fields_together(self):
        safe = self.masker.for_model({"system": "token=demo-repeated", "answer": "demo-repeated"}, self.masker.context())
        self.assertTrue(all(isinstance(value, SanitizedText) for value in safe.values()))
        self.assertNotIn("demo-repeated", repr(safe))


class BoundaryTests(unittest.TestCase):
    def test_preview_requires_confirmation_before_client_call(self):
        client = Mock()
        orchestrator = make_orchestrator(client)
        session, preview = orchestrator.mask_preview("Initial synthetic context", [])
        with self.assertRaises(PreviewReviewRequired) as result:
            orchestrator.generate_draft(session, preview + " password=demo-new")
        client.generate_draft.assert_not_called()
        sanitized = result.exception.masked_text
        client.generate_draft.return_value = {"summary": "Safe draft"}
        orchestrator.generate_draft(session, sanitized)
        received = client.generate_draft.call_args.args[0]
        self.assertIsInstance(received, SanitizedText)
        self.assertNotIn("demo-new", received.text)

    def test_generated_output_sanitized(self):
        client = Mock()
        client.generate_draft.return_value = {"summary": "token=demo-output", "requirements": "demo-output"}
        orchestrator = make_orchestrator(client)
        session, preview = orchestrator.mask_preview("Synthetic context")
        draft = orchestrator.generate_draft(session, preview).draft
        self.assertNotIn("demo-output", repr(draft))

    def test_generated_unknown_keys_and_nonstring_values_rejected(self):
        for result in [{"password=demo-key": "value"}, {"summary": {"password": "demo-nested"}}]:
            client = Mock()
            client.generate_draft.return_value = result
            orchestrator = make_orchestrator(client)
            session, preview = orchestrator.mask_preview("Synthetic context")
            with self.assertRaisesRegex(ValueError, "^Invalid generated draft$"):
                orchestrator.generate_draft(session, preview)

    def test_stub_rejects_raw_text(self):
        with self.assertRaises(TypeError):
            LLMClient().generate_draft("password=demo-pass", get_template("requirements"))

    def test_ticket_edits_sanitized_before_adapter(self):
        orchestrator = make_orchestrator()
        session, _ = orchestrator.mask_preview("Synthetic context")
        spy = Mock(wraps=orchestrator._jira_adapter)
        orchestrator._jira_adapter = spy
        draft = complete_draft()
        draft["summary"] = "password=demo-ticket"
        draft["requirements"] = "demo-ticket"
        ticket = orchestrator.create_ticket(session, draft, reference())
        self.assertNotIn("demo-ticket", repr(spy.create_ticket.call_args))
        self.assertNotIn("demo-ticket", repr(ticket))

    def test_invalid_session_rejected_before_jira(self):
        orchestrator = make_orchestrator()
        orchestrator._jira_adapter = Mock()
        draft = complete_draft()
        draft["summary"] = "api_key=demo-ticket"
        with self.assertRaises(SessionUnavailableError):
            orchestrator.create_ticket("A" * 22, draft, reference())
        orchestrator._jira_adapter.create_ticket.assert_not_called()

    def test_adapter_direct_calls_sanitized_before_storage(self):
        adapter = JiraAdapter()
        draft = complete_draft()
        draft["summary"] = "Authorization: Basic ZGVtbzpwYXNz"
        draft["requirements"] = "ZGVtbzpwYXNz"
        ticket = adapter.create_ticket(draft, get_template("requirements"))
        self.assertNotIn("ZGVtbzpwYXNz", repr(ticket))
        self.assertNotIn("ZGVtbzpwYXNz", repr(adapter._tickets))


class ConnectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Do not import real app.config: that module loads local dotenv values.
        cls.config_patch = patch.dict(sys.modules, {"app.config": SimpleNamespace(OPENAI_API_KEY="", OPENAI_MODEL="synthetic-model")})
        cls.config_patch.start()
        cls.module = importlib.import_module("app.openai_client")

    @classmethod
    def tearDownClass(cls):
        cls.config_patch.stop()

    def client(self):
        client = self.module.OpenAIClient()
        client._client = Mock()
        return client

    def test_connector_rejects_raw_input_without_sdk_call(self):
        client = self.client()
        with self.assertRaises(TypeError):
            client.ask("raw text", SanitizedText("safe"), SyntheticResponse)
        client._client.responses.parse.assert_not_called()

    def test_connector_rechecks_both_messages(self):
        client = self.client()
        client._client.responses.parse.return_value = SimpleNamespace(
            status="completed", output=[], output_parsed=SyntheticResponse()
        )
        client.ask(SanitizedText("password=demo-provider"), SanitizedText("demo-provider"), SyntheticResponse)
        self.assertNotIn("demo-provider", repr(client._client.responses.parse.call_args))
        self.assertFalse(client._client.responses.parse.call_args.kwargs["store"])

    def test_provider_error_text_and_context_not_exposed(self):
        client = self.client()
        client._client.responses.parse.side_effect = OpenAIError("password=demo-provider-error")
        with self.assertRaises(self.module.LLMError) as result:
            client.ask(SanitizedText("safe"), SanitizedText("safe"), SyntheticResponse)
        self.assertEqual(str(result.exception), "Model request failed")
        self.assertIsNone(result.exception.__context__)
        self.assertIsNone(result.exception.__cause__)


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.orchestrator = make_orchestrator()
        self.override = patch("app.main.orchestrator", self.orchestrator)
        self.override.start()
        self.client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://synthetic.test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.override.stop()

    async def test_preview_confirmation_generation_ticket_flow(self):
        response = await self.client.post("/api/mask-preview", json={"text": "password=demo-initial"})
        self.assertEqual(response.status_code, 200)
        preview = response.json()
        self.assertNotIn("demo-initial", response.text)
        payload = {"session_id": preview["session_id"], "masked_text": preview["masked_text"] + " token=demo-edit"}
        response = await self.client.post("/api/generate-draft", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "preview_review_required")
        self.assertNotIn("demo-edit", response.text)
        payload["masked_text"] = response.json()["masked_text"]
        response = await self.client.post("/api/generate-draft", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn("draft", response.json())
        draft = complete_draft()
        draft["requirements"] = "password=demo-ticket-api"
        response = await self.client.post("/api/create-ticket", json={"session_id": preview["session_id"], "template": reference().model_dump(), "draft": draft})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("demo-ticket-api", response.text)
        self.assertTrue(response.json()["key"].startswith("MOCK-"))

    async def test_validation_errors_do_not_echo_inputs_or_keys(self):
        for body in [{"text": {"password=demo-body": "token=demo-body"}},
                     {"session_id": "x", "draft": {"password=demo-key": {"nested": "token=demo-body"}}}]:
            route = "/api/mask-preview" if "text" in body else "/api/create-ticket"
            response = await self.client.post(route, json=body)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json(), {"detail": {"code": "INVALID_REQUEST", "message": "Invalid request"}})
        response = await self.client.post("/api/mask-preview", content='{"password=demo-json"', headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("demo-json", response.text)

    async def test_generated_api_output_sanitized(self):
        self.orchestrator._llm_client = Mock()
        self.orchestrator._llm_client.generate_draft.return_value = {"summary": "password=demo-generated"}
        preview = (await self.client.post("/api/mask-preview", json={"text": "Synthetic context"})).json()
        response = await self.client.post("/api/generate-draft", json={"session_id": preview["session_id"], "masked_text": preview["masked_text"]})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("demo-generated", response.text)

    async def test_unexpected_processing_errors_are_not_reraised(self):
        self.orchestrator.mask_preview = Mock(side_effect=RuntimeError("password=demo-error"))
        response = await self.client.post("/api/mask-preview", json={"text": "Synthetic context"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Processing failed"})


if __name__ == "__main__":
    unittest.main()
