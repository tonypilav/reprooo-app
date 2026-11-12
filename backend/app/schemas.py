from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, StringConstraints, AfterValidator

from app.session_store import SESSION_ID_PATTERN
from app.limits import CONTEXT_CHARS, PREVIEW_CHARS, FIELD_CHARS, valid_draft_size


def validate_draft_size(draft):
    if not valid_draft_size(draft):
        raise ValueError("Invalid draft size")
    return draft


DraftSection = Literal['summary', 'business_context', 'requirements', 'acceptance_criteria', 'open_questions']
DraftText = Annotated[str, StringConstraints(strict=True, max_length=FIELD_CHARS)]
Draft = Annotated[dict[DraftSection, DraftText],
                  Field(max_length=5), AfterValidator(validate_draft_size)]


SessionId = Annotated[str, StringConstraints(
    strict=True, min_length=22, max_length=22, pattern=SESSION_ID_PATTERN
)]


class MaskPreviewRequest(BaseModel):
    text: Annotated[str, StringConstraints(strict=True, max_length=CONTEXT_CHARS)]
    enabled_rules: Annotated[list[Annotated[str, StringConstraints(strict=True, max_length=100)]], Field(max_length=20)] | None = None


class MaskPreviewResponse(BaseModel):
    session_id: str
    masked_text: Annotated[str, StringConstraints(strict=True, max_length=PREVIEW_CHARS)]


class MaskRuleResponse(BaseModel):
    key: str
    label: str
    description: str
    enabled_by_default: bool
    locked: bool

    
class GenerateDraftRequest(BaseModel):
    session_id: SessionId
    masked_text: Annotated[str, StringConstraints(strict=True, max_length=PREVIEW_CHARS)]


class TemplateReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Annotated[StrictStr, Field(max_length=100)]
    version: StrictInt


class GenerateDraftResponse(BaseModel):
    revision: int = 1
    template: TemplateReference
    draft: Draft
    missing_fields: list[str]


class PreviewReviewResponse(BaseModel):
    """Generation was not attempted; review masked_text and submit again."""

    status: Literal["preview_review_required"] = "preview_review_required"
    masked_text: Annotated[str, StringConstraints(strict=True, max_length=PREVIEW_CHARS)]


class CreateTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: Annotated[StrictInt, Field(ge=1)] | None = None
    request_id: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")] | None = None
    session_id: SessionId
    template: TemplateReference
    draft: Draft


class CreateTicketResponse(BaseModel):
    key: str
    url: str
    summary: str
    description: str


class TemplateFieldResponse(BaseModel):
    key: str
    label: str
    required: bool
    hint: str


class TemplateResponse(BaseModel):
    mode: str
    version: int
    fields: list[TemplateFieldResponse]
