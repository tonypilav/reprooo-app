from dataclasses import dataclass

from app.llm_client import LLMClient
from app.masking import Masker
from app.session_store import SessionReservation, SessionStore
from app.template_config import get_active_template
from app.validation import InvalidDraftError, Validator
from app.schemas import TemplateReference
from app.jira_adapter import CreatedTicket, JiraAdapter


class PreviewReviewRequired(Exception):
    def __init__(self, masked_text: str):
        super().__init__("Sanitized preview requires review")
        self.masked_text = masked_text


class IncompleteDraftError(Exception):
    def __init__(self, missing_fields: list[str]):
        super().__init__("Draft is incomplete")
        self.missing_fields = missing_fields


@dataclass(frozen=True)
class GeneratedDraft:
    template: TemplateReference
    draft: dict[str, str]
    missing_fields: list[str]
    revision: int = 1


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

    def admit_session(self, session_id: str) -> SessionReservation:
        return self._session_store.admit(session_id)

    def generate_draft(
        self, session_id: str, masked_text: str,
        *, reservation: SessionReservation | None = None,
    ) -> GeneratedDraft:
        review_text = None
        with self._session_store.operation(session_id, reservation=reservation) as context:
            template = get_active_template()
            model_text = self._masker.for_model({"text": masked_text}, context)["text"]
            if model_text.text != masked_text:
                review_text = model_text.text
            else:
                if context.refinement and context.refinement['revision'] > 1:
                    from app.refinement import RevisionConflict
                    raise RevisionConflict()
                draft_with_masks = self._llm_client.generate_draft(model_text, template)
                try:
                    normalized = self._validator.normalize(draft_with_masks, template)
                except InvalidDraftError:
                    raise ValueError("Invalid generated draft") from None
                draft = self._masker.sanitize_fields(normalized, context)
                missing_fields = self._validator.find_missing(draft, template)
                from app.refinement import initialize
                initialize(context, draft)
        # Review-required is successful activity: commit metadata/TTL before
        # translating it to the existing user-facing response.
        if review_text is not None:
            raise PreviewReviewRequired(review_text)
        return GeneratedDraft(
            TemplateReference(mode=template.mode, version=template.version), draft, missing_fields,
            context.refinement["revision"]
        )

    def create_ticket(
        self, session_id: str, draft: dict[str, str], template_ref: TemplateReference,
        *, reservation: SessionReservation | None = None,
        expected_revision: int | None = None, request_id: str | None = None,
    ) -> CreatedTicket:
        with self._session_store.operation(
            session_id, consume=True, reservation=reservation
        ) as context:
            template = get_active_template()
            if (template_ref.mode != template.mode or template_ref.version != template.version):
                raise InvalidDraftError()
            draft = self._validator.normalize(draft, template)
            if (expected_revision is None) != (request_id is None):
                raise InvalidDraftError()
            if expected_revision is not None:
                from app.refinement import RevisionConflict, ReviewNeeded
                from app.refinement_schemas import RefinementReview
                if not request_id or not context.refinement or expected_revision != context.refinement['revision']:
                    raise RevisionConflict()
                safe = self._masker.sanitize_fields(draft, context)
                if safe != draft:
                    raise ReviewNeeded(RefinementReview(draft=safe))
                context.refinement['draft'] = safe
                context.refinement['revision'] += 1
            draft = self._masker.sanitize_fields(draft, context)
            missing_fields = self._validator.find_missing(draft, template)
            if missing_fields:
                raise IncompleteDraftError(missing_fields)
            ticket = self._jira_adapter.create_ticket(draft, template)
        return ticket

    def delete_session(
        self, session_id: str, *, reservation: SessionReservation | None = None
    ) -> None:
        self._session_store.delete(session_id, reservation=reservation)
