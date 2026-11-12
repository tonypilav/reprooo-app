import os
from threading import Lock
from app.abuse_protection import (AbuseMiddleware, ProtectionError, protection, protection_response, trusted_networks)
from app.safe_logging import configure_safe_logging
import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from functools import partial

from app.refinement import RefinementService, RevisionConflict, ReviewNeeded
from app.refinement_schemas import AnalyzeRequest, RefineRequest, DecisionsRequest, Checkpoint, RefinementReview

from anyio import to_thread

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_cors_origins, get_storage_limit
from app.body_limit import RequestBodyLimitMiddleware
from app.limits import CapacityError, RefinementLimitError
from app.llm_client import create_llm_client
from app.openai_client import LLMError, LLMTimeoutError
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
from app.validation import InvalidDraftError, Validator
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


get_storage_limit("MAX_REFINEMENT_HISTORY_QUESTIONS", 40)
trusted_networks(os.getenv("FORWARDED_ALLOW_IPS", ""))
configure_safe_logging()
app = FastAPI(lifespan=lifespan)

@app.exception_handler(ProtectionError)
async def protection_error(request, error):
    return protection_response(error)

@app.exception_handler(CapacityError)
async def capacity_exhausted(request, error):
    return JSONResponse(status_code=503, content={"detail": {
        "code": "CAPACITY_EXHAUSTED", "message": "Server capacity is exhausted. Try again later."}})


@app.exception_handler(RefinementLimitError)
async def refinement_limit(request, error):
    return JSONResponse(status_code=409, content={"detail": {
        "code": "REFINEMENT_LIMIT_REACHED", "message": "Refinement limit reached. Existing session state is preserved."}})


@app.exception_handler(RevisionConflict)
async def revision_conflict(request, error):
    return JSONResponse(status_code=409, content={'detail': {
        'code': 'REVISION_CONFLICT', 'message': 'Reload the session checkpoint to continue.'}})

@app.exception_handler(ReviewNeeded)
async def refinement_review(request, error):
    return JSONResponse(content=error.review.model_dump())



@app.exception_handler(SessionUnavailableError)
async def session_unavailable(request, error):
    return JSONResponse(status_code=404, content={"detail": {
        "code": "SESSION_UNAVAILABLE",
        "message": "Session is unavailable. Restart the workflow.",
    }})


@app.exception_handler(LLMError)
async def llm_error(request, error):
    # Fixed categories only: never log exception text, payloads, or tracebacks.
    logger.warning("LLM failure category=%s", error.category)
    timeout = isinstance(error, LLMTimeoutError)
    return JSONResponse(status_code=504 if timeout else 502, content={"detail": {
        "code": "LLM_TIMEOUT" if timeout else "LLM_GENERATION_FAILED",
        "message": "Generation timed out" if timeout else "Generation failed",
    }})


@app.exception_handler(InvalidDraftError)
async def invalid_draft(request, error):
    return invalid_request_response()


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
        logger.warning("Request processing failed")
        return JSONResponse(status_code=500, content={"detail": "Processing failed"})

app.add_middleware(AbuseMiddleware, networks=trusted_networks(os.getenv("TRUSTED_PROXY_CIDRS", "")))
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)
orchestrator = Orchestrator(Masker(), session_store, create_llm_client(), Validator(), JiraAdapter())


async def run_session_operation(function, session_id, *args):
    # This async step runs after request validation, before worker scheduling.
    # It never waits on the per-session operation lock on the event loop.
    capacity = protection.operation()
    capacity.__enter__()
    ownership = Lock()
    state = ['pending']
    try:
        reservation = orchestrator.admit_session(session_id)
    except BaseException:
        capacity.__exit__(None, None, None)
        raise

    def worker():
        with ownership:
            if state[0] != 'pending':
                return
            state[0] = 'running'
        try:
            return function(session_id, *args, reservation=reservation)
        finally:
            with ownership:
                state[0] = 'released'
                capacity.__exit__(None, None, None)
    try:
        return await to_thread.run_sync(worker, abandon_on_cancel=True)
    finally:
        with ownership:
            if state[0] == 'pending':
                state[0] = 'released'
                capacity.__exit__(None, None, None)
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
        version=template.version,
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
        result = await run_session_operation(
            orchestrator.generate_draft, request.session_id, request.masked_text
        )
    except PreviewReviewRequired as review:
        return PreviewReviewResponse(masked_text=review.masked_text)
    return GenerateDraftResponse(
        template=result.template, draft=result.draft, missing_fields=result.missing_fields, revision=result.revision
    )

@app.post("/api/create-ticket", response_model=CreateTicketResponse | RefinementReview)
async def create_ticket(request: CreateTicketRequest):
    try:
        ticket = await run_session_operation(
            partial(orchestrator.create_ticket, expected_revision=request.expected_revision, request_id=request.request_id), request.session_id, request.draft, request.template
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


@app.post('/api/clarifications/analyze', response_model=Checkpoint | RefinementReview)
async def analyze_clarifications(request: AnalyzeRequest):
    service = RefinementService(orchestrator)
    return await run_session_operation(service.mutate, request.session_id, 'analyze', request)


@app.post('/api/clarifications/refine', response_model=Checkpoint | RefinementReview)
async def refine_clarifications(request: RefineRequest):
    service = RefinementService(orchestrator)
    return await run_session_operation(service.mutate, request.session_id, 'refine', request)


@app.post('/api/clarifications/decisions', response_model=Checkpoint | RefinementReview)
async def decide_clarifications(request: DecisionsRequest):
    service = RefinementService(orchestrator)
    return await run_session_operation(service.mutate, request.session_id, 'decisions', request)


@app.get('/api/sessions/{session_id}/refinement', response_model=Checkpoint)
async def read_refinement(session_id: SessionId):
    return await run_session_operation(RefinementService(orchestrator).read, session_id)
