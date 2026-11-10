import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from functools import partial

from anyio import to_thread

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.llm_client import LLMClient
from app.masking import Masker
from app.masking_config import MASK_RULES
from app.jira_adapter import JiraAdapter
from app.orchestrator import IncompleteDraftError, Orchestrator, PreviewReviewRequired
from app.schemas import (
    PreviewReviewResponse,
    SessionId,
    GenerateDraftRequest,
    GenerateDraftResponse,
    MaskPreviewRequest,
    MaskPreviewResponse,
    MaskRuleResponse,
    CreateTicketRequest,
    CreateTicketResponse,
    TemplateResponse,
    TemplateFieldResponse,
)
from app.session_store import InvalidSessionIdError, SessionStore, SessionUnavailableError
from app.validation import Validator
from app.jira_adapter import JiraAdapter
from fastapi.middleware.cors import CORSMiddleware
from app.template_config import get_template



session_store = SessionStore()
logger = logging.getLogger(__name__)
CLEANUP_INTERVAL_SECONDS = 30


def cleanup_safely(store):
    try:
        store.cleanup_expired()
    except Exception:
        logger.warning("Session cleanup failed; will retry")


async def cleanup_loop(store, wait=asyncio.sleep):
    while True:
        await wait(CLEANUP_INTERVAL_SECONDS)
        cleanup_safely(store)


@asynccontextmanager
async def lifespan(application):
    cleanup_safely(session_store)
    task = asyncio.create_task(cleanup_loop(session_store), name="session-cleanup")
    application.state.session_cleanup_task = task
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(lifespan=lifespan)


@app.exception_handler(SessionUnavailableError)
async def session_unavailable(request, error):
    return JSONResponse(status_code=404, content={"detail": {
        "code": "SESSION_UNAVAILABLE",
        "message": "Session is unavailable. Restart the workflow.",
    }})


@app.exception_handler(InvalidSessionIdError)
async def invalid_session_id(request, error):
    return invalid_request_response()


def invalid_request_response():
    return JSONResponse(status_code=422, content={"detail": {
        "code": "INVALID_REQUEST", "message": "Invalid request",
    }})


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request, error):
    return invalid_request_response()


@app.middleware("http")
async def safe_processing_errors(request, call_next):
    # Catch before ServerErrorMiddleware can re-raise/log sensitive exceptions.
    try:
        return await call_next(request)
    except Exception:
        return JSONResponse(status_code=500, content={"detail": "Processing failed"})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
orchestrator = Orchestrator(Masker(), session_store, LLMClient(), Validator(), JiraAdapter())


async def run_session_operation(function, session_id, *args):
    # This async step runs after request validation, before worker scheduling.
    # It never waits on the per-session operation lock on the event loop.
    reservation = orchestrator.admit_session(session_id)
    try:
        return await to_thread.run_sync(
            partial(function, session_id, *args, reservation=reservation),
            abandon_on_cancel=True,
        )
    finally:
        # If no worker claimed it, cancel unused admission. Otherwise the worker
        # alone releases it in operation.finally, even if this await is cancelled.
        reservation.cancel()


@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/templates/{mode}", response_model=TemplateResponse)
def read_template(mode: str):
    try:
        template = get_template(mode)
    except KeyError:
        raise HTTPException(status_code=404, detail="Template not found")
    return TemplateResponse(
        mode=template.mode,
        fields=[
            TemplateFieldResponse(
                key=field.key,
                label=field.label,
                required=field.required,
                hint=field.hint,
            )
            for field in template.fields
        ],
    )

@app.get("/api/mask-rules", response_model=list[MaskRuleResponse])
def read_mask_rules():
    return [
        MaskRuleResponse(
            key=rule.key,
            label=rule.label,
            description=rule.description,
            enabled_by_default=rule.enabled_by_default,
            locked=rule.locked,
        )
        for rule in MASK_RULES
    ]


@app.post("/api/mask-preview", response_model=MaskPreviewResponse)
def mask_preview(request: MaskPreviewRequest):
    session_id, masked_text = orchestrator.mask_preview(
        request.text, request.enabled_rules
    )
    return MaskPreviewResponse(session_id=session_id, masked_text=masked_text)


@app.post("/api/generate-draft", response_model=GenerateDraftResponse | PreviewReviewResponse)
async def generate_draft(request: GenerateDraftRequest):
    try:
        draft, missing_fields = await run_session_operation(
            orchestrator.generate_draft, request.session_id, request.masked_text
        )
    except PreviewReviewRequired as review:
        return PreviewReviewResponse(masked_text=review.masked_text)
    return GenerateDraftResponse(draft=draft, missing_fields=missing_fields)

@app.post("/api/create-ticket", response_model=CreateTicketResponse)
async def create_ticket(request: CreateTicketRequest):
    try:
        ticket = await run_session_operation(
            orchestrator.create_ticket, request.session_id, request.draft
        )
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
async def delete_session(session_id: SessionId):
    await run_session_operation(orchestrator.delete_session, session_id)
    return {"status": "deleted"}
