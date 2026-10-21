from fastapi import FastAPI

from app.masking import Masker
from app.schemas import MaskPreviewRequest, MaskPreviewResponse

app = FastAPI()
masker = Masker()


@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.post("/api/mask-preview", response_model=MaskPreviewResponse)
def mask_preview(request: MaskPreviewRequest):
    masked_text, mapping = masker.mask(request.text)
    return MaskPreviewResponse(masked_text=masked_text, mapping=mapping)