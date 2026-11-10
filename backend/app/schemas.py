from typing import Literal

from pydantic import BaseModel


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
    session_id: str
    masked_text: str


class GenerateDraftResponse(BaseModel):
    draft: dict[str, str]
    missing_fields: list[str]


class PreviewReviewResponse(BaseModel):
    """Generation was not attempted; review masked_text and submit again."""

    status: Literal["preview_review_required"] = "preview_review_required"
    masked_text: str


class CreateTicketRequest(BaseModel):
    session_id: str
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
    fields: list[TemplateFieldResponse]
