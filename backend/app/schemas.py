from pydantic import BaseModel


class MaskPreviewRequest(BaseModel):
    text: str


class MaskPreviewResponse(BaseModel):
    session_id: str
    masked_text: str

    
class GenerateDraftRequest(BaseModel):
    session_id: str
    masked_text: str


class GenerateDraftResponse(BaseModel):
    draft: dict[str, str]
    missing_fields: list[str]


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
