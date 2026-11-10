from app.llm_client import LLMClient
from app.masking import Masker
from app.session_store import SessionStore
from app.template_config import get_template
from app.validation import Validator
from app.jira_adapter import CreatedTicket, JiraAdapter


class SessionNotFoundError(Exception):
    pass

class PreviewReviewRequired(Exception):
    def __init__(self, masked_text: str):
        super().__init__("Sanitized preview requires review")
        self.masked_text = masked_text


class IncompleteDraftError(Exception):
    def __init__(self, missing_fields: list[str]):
        super().__init__("Draft is incomplete")
        self.missing_fields = missing_fields


class Orchestrator:
    def __init__(
        self,
        masker: Masker,
        session_store: SessionStore,
        llm_client: LLMClient,
        validator: Validator,
        jira_adapter: JiraAdapter,
    ):
        self._masker = masker
        self._session_store = session_store
        self._llm_client = llm_client
        self._validator = validator
        self._jira_adapter = jira_adapter

    def mask_preview(
        self, text: str, enabled_rules: list[str] | None = None
    ) -> tuple[str, str]:
        enabled_keys = set(enabled_rules) if enabled_rules is not None else None
        masked_text, context = self._masker.mask(text, enabled_keys)
        session_id = self._session_store.create(context)
        return session_id, masked_text

    def generate_draft(
        self, session_id: str, masked_text: str, mode: str = "bug"
    ) -> tuple[dict[str, str], list[str]]:
        context = self._session_store.get(session_id)
        if context is None:
            raise SessionNotFoundError(session_id)
        template = get_template(mode)
        model_text = self._masker.for_model({"text": masked_text}, context)["text"]
        if model_text.text != masked_text:
            raise PreviewReviewRequired(model_text.text)
        draft_with_masks = self._llm_client.generate_draft(model_text, template)
        allowed = {field.key for field in template.fields}
        if (not isinstance(draft_with_masks, dict)
                or any(key not in allowed or not isinstance(value, str)
                       for key, value in draft_with_masks.items())):
            raise ValueError("Invalid generated draft")
        draft = self._masker.sanitize_fields(draft_with_masks, context)
        missing_fields = self._validator.find_missing(draft, template)

        return draft, missing_fields

    def create_ticket(
        self, session_id: str, draft: dict[str, str], mode: str = "bug"
    ) -> CreatedTicket:
        template = get_template(mode)
        # Security protection must not depend on session validity (Task 2).
        context = self._session_store.get(session_id) or self._masker.context()
        draft = self._masker.sanitize_fields(draft, context)
        missing_fields = self._validator.find_missing(draft, template)
        if missing_fields:
            raise IncompleteDraftError(missing_fields)
        ticket = self._jira_adapter.create_ticket(draft, template)
        self._session_store.delete(session_id)
        return ticket

    def delete_session(self, session_id: str) -> None:
        if self._session_store.get(session_id) is None:
            raise SessionNotFoundError(session_id)
        self._session_store.delete(session_id)

        