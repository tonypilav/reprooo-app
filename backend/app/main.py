from fastapi import FastAPI, HTTPException

from app.llm_client import LLMClient
from app.masking import Masker
from app.orchestrator import Orchestrator, SessionNotFoundError
from app.schemas import (
    GenerateDraftRequest,
    GenerateDraftResponse,
    MaskPreviewRequest,
    MaskPreviewResponse,
)
from app.session_store import SessionStore

app = FastAPI()
orchestrator = Orchestrator(Masker(), SessionStore(), LLMClient())


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/mask-preview", response_model=MaskPreviewResponse)
def mask_preview(request: MaskPreviewRequest):
    session_id, masked_text = orchestrator.mask_preview(request.text)
    return MaskPreviewResponse(session_id=session_id, masked_text=masked_text)


@app.post("/api/generate-draft", response_model=GenerateDraftResponse)
def generate_draft(request: GenerateDraftRequest):
    try:
        draft = orchestrator.generate_draft(request.session_id, request.masked_text)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    return GenerateDraftResponse(draft=draft)


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    try:
        orchestrator.delete_session(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}