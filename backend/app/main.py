from fastapi import FastAPI, HTTPException

from app.llm_client import LLMClient
from app.masking import Masker
from app.jira_adapter import JiraAdapter
from app.orchestrator import IncompleteDraftError, Orchestrator, SessionNotFoundError
from app.schemas import (
    GenerateDraftRequest,
    GenerateDraftResponse,
    MaskPreviewRequest,
    MaskPreviewResponse,
    CreateTicketRequest,
    CreateTicketResponse,
)
from app.session_store import SessionStore
from app.validation import Validator
from app.jira_adapter import JiraAdapter


app = FastAPI()
orchestrator = Orchestrator(Masker(), SessionStore(), LLMClient(), Validator(), JiraAdapter())


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
        draft, missing_fields = orchestrator.generate_draft(
            request.session_id, request.masked_text
        )
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    return GenerateDraftResponse(draft=draft, missing_fields=missing_fields)

@app.post("/api/create-ticket", response_model=CreateTicketResponse)
def create_ticket(request: CreateTicketRequest):
    try:
        ticket = orchestrator.create_ticket(request.session_id, request.draft)
    except IncompleteDraftError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Draft is incomplete",
                "missing_fields": error.missing_fields,
            },
        )
    return CreateTicketResponse(
        key=ticket.key,
        url=ticket.url,
        summary=ticket.summary,
        description=ticket.description,
    )


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    try:
        orchestrator.delete_session(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}