"""Session-local clarification operations. All state is committed by SessionStore."""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import uuid
from collections import Counter
from pydantic import ValidationError
from app.masking import SanitizedText
from app.refinement_schemas import (
    QuestionBatch, ProposalBatch, Checkpoint, RefinementReview, sanitize_refinement_content,
)
from app.schemas import TemplateReference
from app.template_config import get_active_template
from app.validation import InvalidDraftError
from app.openai_client import LLMError

_REPLAY_KEY = secrets.token_bytes(32)
logger = logging.getLogger(__name__)

class RevisionConflict(Exception):
    pass

class ReviewNeeded(Exception):
    def __init__(self, review):
        self.review = review


def review_receipt(session_id, revision, safe):
    # Only sanitized replacements cross the review boundary. No raw lookup table.
    body = dict(session_id=session_id, revision=revision,
                history=safe['history'], proposals=safe['proposals'])
    encoded = base64.urlsafe_b64encode(json.dumps(body, ensure_ascii=False).encode()).decode()
    signature = hmac.new(_REPLAY_KEY, encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + '.' + signature


def apply_review_receipt(token, session_id, state):
    try:
        encoded, signature = token.split('.')
        expected = hmac.new(_REPLAY_KEY, encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        body = json.loads(base64.b64decode(encoded, altchars=b'-_', validate=True))
        if body['session_id'] != session_id or body['revision'] != state['revision']:
            raise ValueError()
        state['ledger'] = body['history']
        current = {q['id']: q for q in state['ledger']}
        state['questions'] = [current[q['id']] for q in state['questions']]
        state['proposals'] = body['proposals']
    except (ValueError, KeyError, TypeError):
        raise InvalidDraftError() from None


def initialize(context, draft):
    revision = context.refinement.get("revision", 0) + 1
    context.refinement = dict(draft=draft.copy(), revision=revision, questions=[], ledger=[],
                              proposals=[], batch_id=None, base_revision=None,
                              protected_sections=[], replay={}, question_bases={})


def normalized(text):
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in text.casefold()).split())


def section_basis(draft, sections):
    return hashlib.sha256(json.dumps({s: draft[s] for s in sections}, sort_keys=True).encode()).hexdigest()


def checkpoint(state, validator):
    additions = []
    existing = normalized(state['draft']['open_questions'])
    for q in state['ledger']:
        if q.get('superseded_by') or q['status'] in ('answered', 'not_applicable'):
            continue
        if q['status'] == 'left_to_implementation':
            item = 'Решение делегировано реализации: ' + q['text']
        else:
            item = q['text']
        if item not in additions:
            additions.append(item)
    preview = state['draft']['open_questions']
    pending = [item for item in additions
               if normalized(item.removeprefix('Решение делегировано реализации: ')) not in existing]
    if pending:
        preview = '\n'.join(filter(None, [preview, *('- ' + x for x in pending)]))
    return Checkpoint(
        template=TemplateReference(mode='requirements', version=1),
        draft=state['draft'], revision=state['revision'],
        missing_fields=validator.find_missing(state['draft'], get_active_template()),
        questions=state['questions'], question_history=state['ledger'], proposals=state['proposals'],
        batch_id=state['batch_id'], base_revision=state['base_revision'],
        protected_sections=state['protected_sections'], open_questions_preview=preview,
        open_question_items=additions,
        recent_request_ids=list(state['replay']),
    ).model_dump()


def synchronize(state, draft):
    state['draft'] = draft.copy()
    state['proposals'] = [p for p in state['proposals'] if draft[p['section_id']] == p['base_text']]


class RefinementService:
    def __init__(self, orchestrator):
        self.o = orchestrator

    def read(self, session_id, *, reservation=None):
        with self.o._session_store.operation(session_id, reservation=reservation, refresh=False) as context:
            if not context.refinement:
                raise InvalidDraftError()
            return checkpoint(context.refinement, self.o._validator)

    def mutate(self, session_id, kind, request, *, reservation=None):
        o = self.o
        with o._session_store.operation(session_id, reservation=reservation) as context:
            state = context.refinement
            if not state:
                raise InvalidDraftError()
            template = get_active_template()
            if (request.template.mode, request.template.version) != (template.mode, template.version):
                raise InvalidDraftError()
            # A keyed digest is the only retained representation of the raw request.
            digest = hmac.new(_REPLAY_KEY, (kind + json.dumps(request.model_dump(), sort_keys=True)).encode(), hashlib.sha256).hexdigest()
            previous = state['replay'].get(request.request_id)
            if previous:
                if previous != digest:
                    raise RevisionConflict()
                return checkpoint(state, o._validator)
            if request.expected_revision != state['revision']:
                raise RevisionConflict()
            if request.review_token:
                apply_review_receipt(request.review_token, session_id, state)
            draft = o._validator.normalize(request.draft, template)
            answers = [a.model_dump() for a in getattr(request, 'answers', [])]
            # Recheck the WHOLE outbound content together, including stored history.
            outbound = dict(draft=draft, answers=answers, history=state['ledger'],
                            proposals=state['proposals'])
            safe = sanitize_refinement_content(o._masker, context, outbound)
            if safe != outbound:
                # Raising inside operation rolls back both metadata and draft changes.
                raise ReviewNeeded(RefinementReview(draft=safe['draft'], answers=safe['answers'],
                    history=safe['history'], proposals=safe['proposals'],
                    review_token=review_receipt(session_id, state['revision'], safe)))
            synchronize(state, draft)
            if kind == 'analyze':
                self.analyze(context, state)
            elif kind == 'refine':
                self.refine(context, state, answers)
            else:
                self.decide(state, request)
            state['revision'] += 1
            state['replay'][request.request_id] = digest
            while len(state['replay']) > 32:
                del state['replay'][next(iter(state['replay']))]
            return checkpoint(state, o._validator)

    def provider_result(self, context, payload, method, schema):
        o = self.o
        safe = sanitize_refinement_content(o._masker, context, payload)
        try:
            raw = method(SanitizedText(json.dumps(safe, ensure_ascii=False)))
            result = schema.model_validate(raw).model_dump()
            result = sanitize_refinement_content(o._masker, context, result)
            return schema.model_validate(result).model_dump()
        except (ValidationError, ValueError, TypeError, KeyError):
            raise LLMError('Model request failed', category='structured_output') from None

    def analyze(self, context, state):
        result = self.provider_result(context, dict(draft=state['draft'], history=state['ledger']),
                                      self.o._llm_client.analyze_gaps, QuestionBatch)
        active = []
        seen = set()
        ids = [q['id'] for q in result['questions']]
        if len(ids) != len(set(ids)):
            raise LLMError('Model request failed')
        for q in result['questions']:
            identity = normalized(q['text'])
            if not identity or identity in seen:
                continue
            seen.add(identity)
            # Optional evidence is retained only when quoted verbatim. A paraphrase
            # is not supporting evidence, but need not invalidate the question.
            if q['evidence'] and not any(q['evidence'] in state['draft'][s] for s in q['section_ids']):
                q['evidence'] = ''
            old = next((x for x in reversed(state['ledger']) if normalized(x['text']) == identity), None)
            if old:
                if old['status'] in ('answered', 'not_applicable', 'left_to_implementation', 'needs_stakeholder_decision'):
                    # Reconsider only with new quoted evidence in changed relevant content.
                    changed = state['question_bases'][old['id']] != section_basis(state['draft'], old['section_ids'])
                    new_evidence = q['evidence'] and q['evidence'] != old['evidence']
                    if not (changed and new_evidence and set(old['section_ids']) & set(q['section_ids'])):
                        continue
                else:
                    active.append(old)
                    continue
            q.update(id=uuid.uuid4().hex, status='unanswered', answer='')
            if old:
                old['superseded_by'] = q['id']
            state['question_bases'][q['id']] = section_basis(state['draft'], q['section_ids'])
            state['ledger'].append(q)
            active.append(q)
        order = {'blocking': 0, 'recommended': 1, 'optional': 2}
        state['questions'] = sorted(active, key=lambda q: order[q['priority']])
        state['proposals'] = []
        state['batch_id'] = None
        state['base_revision'] = None

    def refine(self, context, state, answers):
        current = {q['id']: q for q in state['ledger']}
        seen = set()
        for answer in answers:
            q = current.get(answer['question_id'])
            if q is None or q.get('superseded_by') or q['id'] in seen:
                raise InvalidDraftError()
            seen.add(q['id'])
            if answer['status'] == 'answered' and not answer['answer'].strip():
                raise InvalidDraftError()
            if answer['status'] != 'answered' and answer['answer']:
                raise InvalidDraftError()
            # Explicit updates can revisit skips or correct an earlier disposition.
            q.update(status=answer['status'], answer=answer['answer'])
        eligible = [s for s in state['draft'] if s not in state['protected_sections']]
        history = [q for q in current.values() if not q.get('superseded_by')]
        result = self.provider_result(context, dict(draft=state['draft'], questions=history,
                                                   eligible_sections=eligible,
                                                   protected_sections=state['protected_sections']),
                                      self.o._llm_client.refine_sections, ProposalBatch)
        proposals = []
        sections = Counter(p['section_id'] for p in result['proposals'])
        excluded = Counter()
        for p in result['proposals']:
            section = p['section_id']
            if sections[section] > 1:
                excluded['duplicate_section'] += 1
                continue
            if section not in eligible:
                excluded['protected_section'] += 1
                continue
            supporting = [current.get(qid) for qid in p['question_ids']]
            if any(not q or q.get('superseded_by') or q['status'] != 'answered'
                   or section not in q['section_ids']
                   or set(q['section_ids']) & set(state['protected_sections'])
                   for q in supporting):
                excluded['unsupported_reference'] += 1
                continue
            if p['replacement'] == state['draft'][section]:
                continue
            p.update(id=uuid.uuid4().hex, base_text=state['draft'][section])
            proposals.append(p)
        for category, count in sorted(excluded.items()):
            logger.warning('Refinement proposals excluded category=%s count=%d', category, count)
        state['proposals'] = proposals
        state['batch_id'] = uuid.uuid4().hex
        state['base_revision'] = state['revision'] + 1
        state['questions'] = [current[q['id']] for q in state['questions']]

    def decide(self, state, request):
        if request.decisions and request.batch_id != state['batch_id']:
            raise RevisionConflict()
        by_id = {p['id']: p for p in state['proposals']}
        seen = set()
        for decision in request.decisions:
            p = by_id.get(decision.proposal_id)
            if not p or p['id'] in seen or state['draft'][p['section_id']] != p['base_text']:
                raise RevisionConflict()
            seen.add(p['id'])
            section = p['section_id']
            if decision.action == 'accept':
                if section in state['protected_sections']:
                    raise RevisionConflict()
                state['draft'][section] = p['replacement']
            elif section not in state['protected_sections']:
                state['protected_sections'].append(section)
        if any(s in request.allow_sections for s in (by_id[x.proposal_id]['section_id'] for x in request.decisions)):
            raise InvalidDraftError()
        state['protected_sections'] = [s for s in state['protected_sections'] if s not in request.allow_sections]
        state['proposals'] = [p for p in state['proposals'] if p['id'] not in seen
                              and p['base_text'] == state['draft'][p['section_id']]]
