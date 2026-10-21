from pydantic import BaseModel


class MaskPreviewRequest(BaseModel):
    text: str


class MaskPreviewResponse(BaseModel):
    session_id: str
    masked_text: str
    
