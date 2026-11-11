from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, StringConstraints

from app.session_store import SESSION_ID_PATTERN


SessionId = Annotated[str, StringConstraints(
    strict=True, min_length=22, max_length=22, pattern=SESSION_ID_PATTERN
)]


class MaskPreviewRequest(BaseModel):
    text: str
    enabled_rules: list[str] | None = None


class MaskPreviewResponse(BaseModel):
    session_id: str
    masked_text: str


class MaskRuleResponse(BaseModel):
    key: str
    label: str
    description: str
    enabled_by_default: bool
    locked: bool

    
class GenerateDraftRequest(BaseModel):
    session_id: SessionId
    masked_text: str


class TemplateReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: StrictStr
    version: StrictInt


class GenerateDraftResponse(BaseModel):
    template: TemplateReference
    draft: dict[str, str]
    missing_fields: list[str]


class PreviewReviewResponse(BaseModel):
    """Generation was not attempted; review masked_text and submit again."""

    status: Literal["preview_review_required"] = "preview_review_required"
    masked_text: str


class CreateTicketRequest(BaseModel):
    session_id: SessionId
    template: TemplateReference
    draft: dict[str, str]


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
