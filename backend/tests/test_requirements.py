"""Requirements-v1 contracts; synthetic data only, no external integrations."""
import unittest
from unittest.mock import Mock, patch

import httpx2
from pydantic import ValidationError

from app import main
from app.jira_adapter import JiraAdapter
from app.llm_client import LLMClient
from app.masking import Masker, SanitizedText
from app.orchestrator import IncompleteDraftError, Orchestrator
from app.schemas import CreateTicketRequest, TemplateReference
from app.session_store import SessionStore
from app.template_config import TEMPLATES, get_active_template, get_template
from app.validation import InvalidDraftError, Validator
from test_sessions import FakeClock, draft, reference


KEYS = ["summary", "business_context", "requirements", "acceptance_criteria", "open_questions"]
REQUIRED = ["summary", "business_context", "acceptance_criteria"]
SAFE_INVALID = {"detail": {"code": "INVALID_REQUEST", "message": "Invalid request"}}


class TemplateAndValidationTests(unittest.TestCase):
    def setUp(self):
        self.template = get_active_template()
        self.validator = Validator()

    def test_exact_registry_identity_order_labels_and_flags(self):
        self.assertEqual(list(TEMPLATES), ["requirements"])
        self.assertIs(get_template("requirements"), self.template)
        self.assertEqual((self.template.mode, self.template.version), ("requirements", 1))
        self.assertEqual([field.key for field in self.template.fields], KEYS)
        self.assertEqual([field.key for field in self.template.fields if field.required], REQUIRED)
        self.assertEqual([field.label for field in self.template.fields], [
            "Краткое описание", "Бизнес-контекст", "Требования", "Критерии приёмки", "Открытые вопросы",
        ])
        self.assertTrue(all(field.hint.strip() for field in self.template.fields))

    def test_unsupported_lookup_has_no_bug_alias(self):
        for mode in ["bug", "unknown"]:
            with self.subTest(mode=mode), self.assertRaises(KeyError):
                get_template(mode)

    def test_normalization_returns_all_keys_in_template_order(self):
        normalized = self.validator.normalize({"open_questions": "Who?", "summary": "  Preserve spacing  "}, self.template)
        self.assertEqual(list(normalized), KEYS)
        self.assertEqual(normalized, {
            "summary": "  Preserve spacing  ", "business_context": "", "requirements": "",
            "acceptance_criteria": "", "open_questions": "Who?",
        })

    def test_empty_generation_is_structurally_valid_but_incomplete(self):
        normalized = self.validator.normalize({}, self.template)
        self.assertEqual(list(normalized), KEYS)
        self.assertEqual(self.validator.find_missing(normalized, self.template), REQUIRED)

    def test_unknown_and_legacy_fields_rejected_without_echo(self):
        for key in ["title", "steps", "actual_result", "password=demo-unknown-key"]:
            with self.subTest(key=key), self.assertRaises(InvalidDraftError) as error:
                self.validator.normalize({**draft(), key: "token=demo-value"}, self.template)
            self.assertEqual(str(error.exception), "Invalid request")

    def test_nonstring_fields_and_nonobject_drafts_rejected(self):
        for value in [None, 1, 1.5, True, [], {}, ["synthetic"]]:
            with self.subTest(value=value), self.assertRaises(InvalidDraftError):
                self.validator.normalize({"summary": value}, self.template)
        for value in [None, [], "synthetic", 1]:
            with self.subTest(draft=value), self.assertRaises(InvalidDraftError):
                self.validator.normalize(value, self.template)

    def test_required_fields_independently_reject_blank_values(self):
        for key in REQUIRED:
            for value in ["", " \n\t "]:
                with self.subTest(key=key, value=value):
                    normalized = self.validator.normalize({**draft(), key: value}, self.template)
                    self.assertEqual(self.validator.find_missing(normalized, self.template), [key])

    def test_optional_fields_may_be_omitted(self):
        normalized = self.validator.normalize({key: "Synthetic requirement" for key in REQUIRED}, self.template)
        self.assertEqual(self.validator.find_missing(normalized, self.template), [])
        self.assertEqual(normalized["requirements"], "")
        self.assertEqual(normalized["open_questions"], "")

    def test_reference_types_are_strict_and_definitions_forbidden(self):
        for value in [{"mode": "requirements", "version": True},
                      {"mode": "requirements", "version": "1"},
                      {"mode": 1, "version": 1},
                      {"mode": "requirements", "version": 1, "fields": []}]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                TemplateReference.model_validate(value)
        with self.assertRaises(ValidationError):
            CreateTicketRequest.model_validate({"session_id": "A" * 22, "draft": draft()})


class GeneratorAndJiraTests(unittest.TestCase):
    def setUp(self):
        self.template = get_active_template()

    def test_stub_literal_mapping_and_no_invented_content(self):
        text = "\n \n  Add a test-message button  \nPlace it in service settings."
        result = LLMClient().generate_draft(SanitizedText(text), self.template)
        self.assertEqual(list(result), KEYS)
        self.assertEqual(result["summary"], "  Add a test-message button  ")
        self.assertEqual(result["requirements"], text)
        for key in ["business_context", "acceptance_criteria", "open_questions"]:
            self.assertEqual(result[key], "")
        self.assertEqual(result, LLMClient().generate_draft(SanitizedText(text), self.template))

    def test_empty_input_does_not_fabricate_summary(self):
        for text in ["", " \n\t"]:
            with self.subTest(text=text):
                result = LLMClient().generate_draft(SanitizedText(text), self.template)
                self.assertEqual(result["summary"], "")
                self.assertEqual(result["requirements"], text)
                self.assertEqual(Validator().find_missing(result, self.template), REQUIRED)

    def test_jira_summary_and_description_order(self):
        fields = {key: f"Synthetic {key}" for key in reversed(KEYS)}
        ticket = JiraAdapter().create_ticket(fields, self.template)
        self.assertEqual(ticket.summary, fields["summary"])
        expected = "\n\n".join(f"{field.label}:\n{fields[field.key]}" for field in self.template.fields if field.key != "summary")
        self.assertEqual(ticket.description, expected)
        self.assertNotIn(fields["summary"], ticket.description)

    def test_jira_omits_blank_optional_sections(self):
        ticket = JiraAdapter().create_ticket({
            "summary": " Synthetic summary ", "business_context": "Synthetic goal",
            "acceptance_criteria": "Synthetic outcome", "requirements": " \n ",
        }, self.template)
        self.assertEqual(ticket.summary, "Synthetic summary")
        self.assertEqual(ticket.description, "Бизнес-контекст:\nSynthetic goal\n\nКритерии приёмки:\nSynthetic outcome")

    def test_direct_adapter_rejects_unknown_fields_before_storage(self):
        adapter = JiraAdapter()
        with self.assertRaises(InvalidDraftError):
            adapter.create_ticket({**draft(), "title": "password=demo-legacy"}, self.template)
        self.assertEqual(adapter._tickets, [])

    def test_generated_secrets_sanitized_across_all_five_fields(self):
        client = Mock()
        client.generate_draft.return_value = {
            "summary": "password=demo-generated", "business_context": "demo-generated",
            "requirements": "demo-generated", "acceptance_criteria": "demo-generated",
            "open_questions": "demo-generated",
        }
        orchestrator = Orchestrator(Masker(), SessionStore(), client, Validator(), JiraAdapter())
        sid, preview = orchestrator.mask_preview("Synthetic context")
        result = orchestrator.generate_draft(sid, preview)
        self.assertEqual(list(result.draft), KEYS)
        self.assertEqual(result.template.model_dump(), {"mode": "requirements", "version": 1})
        self.assertNotIn("demo-generated", repr(result))

    def test_submitted_secret_in_each_field_and_cross_field_repetition(self):
        adapter = JiraAdapter()
        spy = Mock(wraps=adapter)
        store = SessionStore()
        orchestrator = Orchestrator(Masker(), store, LLMClient(), Validator(), spy)
        for key in KEYS:
            with self.subTest(key=key):
                sid, _ = orchestrator.mask_preview("Synthetic context")
                secret = "demo-submitted-" + key
                fields = {field: secret for field in KEYS}
                fields[key] = "password=" + secret
                ticket = orchestrator.create_ticket(sid, fields, reference())
                self.assertNotIn(secret, repr(spy.create_ticket.call_args))
                self.assertNotIn(secret, repr(ticket))
                self.assertNotIn(secret, repr(adapter._tickets))
                self.assertIsNone(store.get(sid))


class RequirementsAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = FakeClock()
        self.store = SessionStore(clock=self.clock)
        self.jira = Mock(wraps=JiraAdapter())
        self.orchestrator = Orchestrator(Masker(), self.store, LLMClient(), Validator(), self.jira)
        self.override = patch("app.main.orchestrator", self.orchestrator)
        self.override.start()
        self.client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), base_url="http://synthetic.test")
        self.sid, self.preview = self.orchestrator.mask_preview("Add a test-message button\nPlace it in service settings.")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.override.stop()

    def payload(self):
        return {"session_id": self.sid, "template": reference().model_dump(), "draft": draft()}

    def assert_failed_without_session_changes(self, response):
        self.assertEqual(response.status_code, 422)
        self.jira.create_ticket.assert_not_called()
        self.assertIsNotNone(self.store.get(self.sid))
        self.assertEqual(self.store._sessions[self.sid].expires_at, 900)
        self.assertEqual(self.store._sessions[self.sid].admitted, 0)
        self.assertEqual(self.store.get(self.sid).issued, {})

    async def test_template_api_identity_fields_and_legacy_404(self):
        response = await self.client.get("/api/templates/requirements")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["mode"], body["version"]), ("requirements", 1))
        self.assertEqual([field["key"] for field in body["fields"]], KEYS)
        response = await self.client.get("/api/templates/bug")
        self.assertEqual(response.status_code, 404)

    async def test_generation_contract_then_synthetic_smoke_submission(self):
        generated = await self.client.post("/api/generate-draft", json={"session_id": self.sid, "masked_text": self.preview})
        self.assertEqual(generated.status_code, 200)
        body = generated.json()
        self.assertEqual(body, {
            "template": {"mode": "requirements", "version": 1},
            "draft": {"summary": "Add a test-message button", "business_context": "",
                      "requirements": self.preview, "acceptance_criteria": "", "open_questions": ""},
            "missing_fields": ["business_context", "acceptance_criteria"],
        })
        body["draft"]["business_context"] = "Allow operators to verify message delivery."
        body["draft"]["acceptance_criteria"] = "Clicking the button sends one test message."
        body["draft"]["open_questions"] = "Which roles may use the button?"
        result = await self.client.post("/api/create-ticket", json={
            "session_id": self.sid, "template": body["template"], "draft": body["draft"],
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["summary"], body["draft"]["summary"])
        self.assertIn("Which roles may use the button?", result.json()["description"])
        self.assertIsNone(self.store.get(self.sid))

    async def test_unsupported_reference_validates_inside_admitted_operation(self):
        admitted = Mock(wraps=self.store.admit)
        self.store.admit = admitted
        self.clock.set(100)
        for ref in [{"mode": "bug", "version": 1}, {"mode": "requirements", "version": 2},
                    {"mode": "unknown", "version": 1}, {"mode": "requirements", "version": 0}]:
            with self.subTest(ref=ref):
                payload = self.payload()
                payload["template"] = ref
                response = await self.client.post("/api/create-ticket", json=payload)
                self.assertEqual(response.json(), SAFE_INVALID)
                self.assert_failed_without_session_changes(response)
        self.assertEqual(admitted.call_count, 4)

    async def test_missing_malformed_reference_and_client_definitions_fail_safely(self):
        self.clock.set(100)
        for ref in [None, {}, {"mode": "requirements"}, {"mode": "requirements", "version": True},
                    {"mode": "requirements", "version": "1"},
                    {"mode": "requirements", "version": 1, "fields": [{"key": "password=demo-key"}]}]:
            with self.subTest(ref=ref):
                payload = self.payload()
                payload["template"] = ref
                response = await self.client.post("/api/create-ticket", json=payload)
                self.assertEqual(response.json(), SAFE_INVALID)
                self.assert_failed_without_session_changes(response)
        payload = self.payload()
        del payload["template"]
        response = await self.client.post("/api/create-ticket", json=payload)
        self.assertEqual(response.json(), SAFE_INVALID)
        self.assert_failed_without_session_changes(response)

    async def test_unknown_and_nonstring_draft_values_are_safe_errors(self):
        self.clock.set(100)
        cases = [{**draft(), "password=demo-key": "token=demo-value"},
                 {**draft(), "title": "Legacy summary"}]
        cases += [{**draft(), "summary": value} for value in [None, 12, True, [], {}, ["demo-value"]]]
        for fields in cases:
            with self.subTest(fields=fields):
                payload = self.payload()
                payload["draft"] = fields
                response = await self.client.post("/api/create-ticket", json=payload)
                self.assertEqual(response.json(), SAFE_INVALID)
                self.assert_failed_without_session_changes(response)
                self.assertNotIn("demo-key", response.text)
                self.assertNotIn("demo-value", response.text)

    async def test_each_required_field_missing_or_whitespace_blocks_submission(self):
        self.clock.set(100)
        for key in REQUIRED:
            for omit in [True, False]:
                payload = self.payload()
                if omit:
                    del payload["draft"][key]
                else:
                    payload["draft"][key] = " \n\t"
                response = await self.client.post("/api/create-ticket", json=payload)
                self.assertEqual(response.json()["detail"]["missing_fields"], [key])
                self.assert_failed_without_session_changes(response)

    async def test_omitted_optional_fields_are_normalized_before_jira(self):
        payload = self.payload()
        payload["draft"] = {key: "Synthetic requirement" for key in REQUIRED}
        response = await self.client.post("/api/create-ticket", json=payload)
        self.assertEqual(response.status_code, 200)
        received = self.jira.create_ticket.call_args.args[0]
        self.assertEqual(list(received), KEYS)
        self.assertEqual(received["requirements"], "")
        self.assertEqual(received["open_questions"], "")

    async def test_unavailable_session_takes_precedence_over_unsupported_reference(self):
        self.clock.set(900)
        payload = self.payload()
        payload["template"] = {"mode": "bug", "version": 2}
        response = await self.client.post("/api/create-ticket", json=payload)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "SESSION_UNAVAILABLE")
        self.jira.create_ticket.assert_not_called()


if __name__ == "__main__":
    unittest.main()
