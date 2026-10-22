from app.llm_client import LLMClient
from app.masking import Masker
from app.session_store import SessionStore
from app.template_config import get_template


class SessionNotFoundError(Exception):
    pass


class Orchestrator:
    def __init__(
        self,
        masker: Masker,
        session_store: SessionStore,
        llm_client: LLMClient,
    ):
        self._masker = masker
        self._session_store = session_store
        self._llm_client = llm_client

    def mask_preview(self, text: str) -> tuple[str, str]:
        masked_text, mapping = self._masker.mask(text)
        session_id = self._session_store.create(mapping)
        return session_id, masked_text

    
    def generate_draft(
    self, session_id: str, masked_text: str, mode: str = "bug"
    ) -> dict[str, str]:
        mapping = self._session_store.get(session_id)
        if mapping is None:
            raise SessionNotFoundError(session_id)
        template = get_template(mode)
        draft_with_masks = self._llm_client.generate_draft(masked_text, template)
        return {
            key: self._masker.unmask(value, mapping)
            for key, value in draft_with_masks.items()
        }

    def delete_session(self, session_id: str) -> None:
        if self._session_store.get(session_id) is None:
            raise SessionNotFoundError(session_id)
        self._session_store.delete(session_id)

        