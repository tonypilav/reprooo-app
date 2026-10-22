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
