from pydantic import BaseModel


class MaskPreviewRequest(BaseModel):
    text: str


class MaskPreviewResponse(BaseModel):
    masked_text: str
    mapping: dict[str, str]
    