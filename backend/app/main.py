from fastapi import FastAPI, HTTPException

from app.masking import Masker
from app.schemas import MaskPreviewRequest, MaskPreviewResponse
from app.session_store import SessionStore

app = FastAPI()
masker = Masker()
session_store = SessionStore()

@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.post("/api/mask-preview", response_model=MaskPreviewResponse)
def mask_preview(request: MaskPreviewRequest):
    masked_text, mapping = masker.mask(request.text)
    session_id = session_store.create(mapping)
    return MaskPreviewResponse(session_id=session_id, masked_text=masked_text)


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    if session_store.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    session_store.delete(session_id)
    return {"status": "deleted"}