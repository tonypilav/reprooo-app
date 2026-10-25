from statistics import mode

from app.llm_client import LLMClient
from app.masking import Masker
from app.session_store import SessionStore
from app.template_config import get_template
from app.validation import Validator
from app.jira_adapter import CreatedTicket, JiraAdapter


class SessionNotFoundError(Exception):
    pass

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
        masked_text, mapping = self._masker.mask(text, enabled_keys)
        session_id = self._session_store.create(mapping)
        return session_id, masked_text

    def generate_draft(
    self, session_id: str, masked_text: str, mode: str = "bug"
    ) -> tuple[dict[str, str], list[str]]:
        mapping = self._session_store.get(session_id)
        if mapping is None:
            raise SessionNotFoundError(session_id)
        template = get_template(mode)
        draft_with_masks = self._llm_client.generate_draft(masked_text, template)
        draft = {
            key: self._masker.unmask(value, mapping)
            for key, value in draft_with_masks.items()
        }
        missing_fields = self._validator.find_missing(draft, template)

        return draft, missing_fields

    def create_ticket(
        self, session_id: str, draft: dict[str, str], mode: str = "bug"
    ) -> CreatedTicket:
        template = get_template(mode)
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

        