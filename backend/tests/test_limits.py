"""Synthetic request/storage boundaries. Never make live provider requests."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
import unittest
from unittest.mock import Mock, patch

import httpx2
from pydantic import ValidationError

from app import main
from app.body_limit import RequestBodyLimitMiddleware
from app.config import get_storage_limit
from app.jira_adapter import JiraAdapter
from app.limits import BODY_BYTES, REVIEW_TOKEN_CHARS, CapacityError, RefinementLimitError
from app.llm_client import LLMClient, RequirementsOutput
from app.masking import Masker
from app.orchestrator import Orchestrator
from app.refinement import review_receipt, section_basis, RevisionConflict, ReviewNeeded
from app.refinement_schemas import AnalyzeRequest, RefineRequest, DecisionsRequest, QuestionState, ProposalBatch
from app.schemas import MaskPreviewRequest, GenerateDraftRequest, CreateTicketRequest
from app.session_store import SessionStore, SessionUnavailableError
from app.template_config import get_active_template
from app.validation import Validator, InvalidDraftError
from test_sessions import FakeClock, reference, draft
import test_refinement


def question(index, text='Synthetic question', answer='', status='unanswered'):
    return dict(id=f'q-{index}', text=f'{text} {index}', section_ids=['requirements'],
                priority='recommended', rationale='', evidence='', status=status, answer=answer)


class BodyMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, chunks, headers=()):
        messages, calls, receives = [], [], []
        chunks = list(chunks)

        async def receive():
            receives.append(True)
            chunk = chunks.pop(0)
            return dict(type='http.request', body=chunk, more_body=bool(chunks))

        async def endpoint(scope, receive, send):
            calls.append(await receive())
            await send(dict(type='http.response.start', status=204, headers=[]))
            await send(dict(type='http.response.body', body=b''))

        await RequestBodyLimitMiddleware(endpoint)(dict(type='http', headers=headers), receive, self.sender(messages))
        return messages, calls, receives

    @staticmethod
    def sender(messages):
        async def send(message):
            messages.append(message)
        return send

    async def test_below_and_exact_limit_without_content_length(self):
        for size in [BODY_BYTES - 1, BODY_BYTES]:
            messages, calls, _ = await self.exercise([b'x' * size])
            self.assertEqual(messages[0]['status'], 204)
            self.assertEqual(len(calls[0]['body']), size)

    async def test_streamed_exact_limit(self):
        messages, calls, _ = await self.exercise([b'x' * 65536] * 4)
        self.assertEqual(messages[0]['status'], 204)
        self.assertEqual(len(calls[0]['body']), BODY_BYTES)

    async def test_stream_overflow_stops_before_reading_remaining_chunks(self):
        messages, calls, receives = await self.exercise([b'x' * BODY_BYTES, b'password=synthetic', b'unread'])
        self.assertEqual(len(receives), 2)
        self.assertEqual(calls, [])
        self.assertEqual(messages[0]['status'], 413)
        self.assertEqual(json.loads(messages[1]['body']), {'detail': {
            'code': 'REQUEST_TOO_LARGE', 'message': 'Request body exceeds the size limit.'}})
        self.assertNotIn(b'synthetic', messages[1]['body'])

    async def test_actual_bytes_override_false_content_length(self):
        messages, calls, _ = await self.exercise([b'x' * (BODY_BYTES + 1)], [(b'content-length', b'1')])
        self.assertEqual(messages[0]['status'], 413)
        self.assertFalse(calls)

    async def test_multibyte_size_is_bytes(self):
        messages, calls, _ = await self.exercise([('界' * (BODY_BYTES // 3 + 1)).encode()])
        self.assertEqual(messages[0]['status'], 413)
        self.assertFalse(calls)


class SchemaLimitTests(unittest.TestCase):
    def test_context_and_preview_boundaries(self):
        MaskPreviewRequest(text='x' * 20000)
        GenerateDraftRequest(session_id='A' * 22, masked_text='x' * 40000)
        for schema, args in [(MaskPreviewRequest, dict(text='x' * 20001)),
                             (GenerateDraftRequest, dict(session_id='A' * 22, masked_text='x' * 40001))]:
            with self.assertRaises(ValidationError):
                schema(**args)

    def test_draft_field_and_aggregate_boundaries(self):
        maximum = dict(summary='x' * 20000, requirements='x' * 20000, business_context='x' * 10000)
        for schema, args in [(CreateTicketRequest, dict(session_id='A' * 22, template=reference())),
                             (AnalyzeRequest, dict(session_id='A' * 22, template=reference(), expected_revision=1, request_id='test'))]:
            schema(**args, draft=maximum)
            for too_big in [dict(summary='x' * 20001), {**maximum, 'acceptance_criteria': 'x'},
                            {str(i): 'x' for i in range(6)}]:
                with self.assertRaises(ValidationError):
                    schema(**args, draft=too_big)
        Validator().normalize(maximum, get_active_template())
        with self.assertRaises(InvalidDraftError):
            Validator().normalize({**maximum, 'acceptance_criteria': 'x'}, get_active_template())

    def test_generated_draft_boundaries(self):
        maximum = dict.fromkeys(draft(), '')
        maximum.update(summary='x' * 20000, requirements='x' * 20000, business_context='x' * 10000)
        RequirementsOutput(**maximum)
        for field, value in [('requirements', 'x' * 20001), ('acceptance_criteria', 'x')]:
            with self.assertRaises(ValidationError):
                RequirementsOutput(**{**maximum, field: value})

    def request(self, **overrides):
        return RefineRequest(session_id='A' * 22, template=reference(), expected_revision=1,
                             request_id='test', draft=draft(), **overrides)

    def test_answers_field_aggregate_and_count_boundaries(self):
        answer = dict(question_id='q', status='answered', answer='x' * 4000)
        self.request(answers=[answer] * 5)
        self.request(answers=[{**answer, 'answer': ''}] * 20)
        for answers in [[{**answer, 'answer': 'x' * 4001}], [answer] * 6, [{**answer, 'answer': ''}] * 21]:
            with self.assertRaises(ValidationError):
                self.request(answers=answers)

    def test_token_boundary(self):
        self.request(review_token='x' * REVIEW_TOKEN_CHARS)
        with self.assertRaises(ValidationError):
            self.request(review_token='x' * (REVIEW_TOKEN_CHARS + 1))

    def test_relevant_lists_and_question_fields(self):
        MaskPreviewRequest(text='synthetic', enabled_rules=['x'] * 20)
        for args in [dict(enabled_rules=['x'] * 21), dict(enabled_rules=['x' * 101])]:
            with self.assertRaises(ValidationError):
                MaskPreviewRequest(text='synthetic', **args)
        q = question(0)
        for key, value in [('text', 'x' * 1001), ('rationale', 'x' * 1001),
                           ('evidence', 'x' * 2001), ('answer', 'x' * 4001), ('section_ids', ['summary'] * 6)]:
            with self.assertRaises(ValidationError):
                QuestionState(**{**q, key: value})
        with self.assertRaises(ValidationError):
            ProposalBatch(proposals=[{}] * 6)

    def test_settings_are_validated_without_echoing_values(self):
        for name, ceiling in [('MAX_ACTIVE_SESSIONS', 100), ('MAX_MOCK_TICKETS', 100),
                              ('MAX_REFINEMENT_HISTORY_QUESTIONS', 40)]:
            for value in ['0', '-1', str(ceiling + 1), 'password=synthetic', '1.5', '']:
                with patch.dict('os.environ', {name: value}):
                    with self.assertRaises(ValueError) as error:
                        get_storage_limit(name, ceiling)
                    self.assertNotIn('synthetic', str(error.exception))
            with patch.dict('os.environ', {name: '1'}):
                self.assertEqual(get_storage_limit(name, ceiling), 1)


class SessionCapacityTests(unittest.TestCase):
    def test_exact_capacity_no_eviction_or_id_generation_after_rejection(self):
        store = SessionStore()
        ids = [store.create(Masker().context()) for _ in range(100)]
        with patch('app.session_store.secrets.token_urlsafe') as generate:
            with self.assertRaises(CapacityError):
                store.create(Masker().context())
            generate.assert_not_called()
        self.assertEqual(set(ids), set(store._sessions))
        self.assertTrue(all(store.get(sid) is not None for sid in ids))

    def test_concurrent_creation_at_100_boundary(self):
        store = SessionStore()
        original = {store.create(Masker().context()) for _ in range(99)}
        barrier = Barrier(24)
        def create(_):
            barrier.wait(timeout=5)
            try:
                return store.create(Masker().context())
            except CapacityError:
                return None
        with ThreadPoolExecutor(max_workers=24) as pool:
            results = list(pool.map(create, range(24)))
        self.assertEqual(sum(sid is not None for sid in results), 1)
        self.assertEqual(len(store._sessions), 100)
        self.assertTrue(original <= set(store._sessions))

    def test_concurrent_cleanup_and_creation_preserve_pinned_expired_session(self):
        clock = FakeClock()
        store = SessionStore(clock=clock)
        ids = [store.create(Masker().context()) for _ in range(100)]
        reservation = store.admit(ids[0])
        clock.set(900)
        barrier = Barrier(24)
        def work(index):
            barrier.wait(timeout=5)
            if index % 2:
                store.cleanup_expired()
            return store.create(Masker().context())
        with ThreadPoolExecutor(max_workers=24) as pool:
            results = list(pool.map(work, range(24)))
        self.assertEqual(len(set(results)), 24)
        self.assertEqual(len(store._sessions), 25)
        self.assertIn(ids[0], store._sessions)
        self.assertFalse(set(ids[1:]) & set(store._sessions))
        # Previously admitted work retains eligibility and sliding completion TTL.
        with store.operation(ids[0], reservation=reservation):
            clock.set(950)
        self.assertEqual(store._sessions[ids[0]].expires_at, 1850)

    def test_pinned_expired_entries_occupy_capacity_until_release(self):
        clock = FakeClock()
        store = SessionStore(clock=clock)
        reservations = [store.admit(store.create(Masker().context())) for _ in range(100)]
        clock.set(900)
        with self.assertRaises(CapacityError):
            store.create(Masker().context())
        reservations[0].cancel()
        store.create(Masker().context())
        self.assertEqual(len(store._sessions), 100)
        for reservation in reservations[1:]:
            reservation.cancel()


class RefinementHistoryLimitTests(unittest.TestCase):
    setUp = test_refinement.RefinementTests.setUp
    read = test_refinement.RefinementTests.read
    req = test_refinement.RefinementTests.req
    analyze = test_refinement.RefinementTests.analyze
    refine = test_refinement.RefinementTests.refine
    decide = test_refinement.RefinementTests.decide

    def seed(self, questions):
        with self.store.operation(self.sid) as context:
            state = context.refinement
            state['ledger'] = questions
            state['questions'] = questions[-5:]
            state['question_bases'] = {q['id']: section_basis(state['draft'], q['section_ids']) for q in questions}
        return self.read()

    def test_history_count_boundary_and_replay_remain_usable(self):
        self.seed([question(i) for i in range(39)])
        self.llm.analyze_gaps = Mock(return_value=dict(questions=[{k: v for k, v in question(39).items() if k not in ('status', 'answer')}]))
        request = self.req()
        state = self.service.mutate(self.sid, 'analyze', request)
        self.assertEqual(len(state['question_history']), 40)
        self.assertEqual(self.service.mutate(self.sid, 'analyze', request), state)
        before = self.store.get(self.sid)
        with self.assertRaises(RefinementLimitError):
            self.analyze()
        self.assertEqual(self.store.get(self.sid), before)
        self.assertEqual(self.llm.analyze_gaps.call_count, 1)

    def test_overflow_rolls_back_manual_edits_and_partial_questions(self):
        self.seed([question(i) for i in range(39)])
        self.llm.analyze_gaps = Mock(return_value=dict(questions=[{k: v for k, v in question(i).items() if k not in ('status', 'answer')} for i in (39, 40)]))
        before = self.store.get(self.sid)
        changed = {**self.read()['draft'], 'requirements': 'Edited synthetic requirement'}
        with self.assertRaises(RefinementLimitError):
            self.analyze(draft=changed)
        self.assertEqual(self.store.get(self.sid), before)

    def test_history_aggregate_exact_boundary_and_overflow_before_provider(self):
        qs = [question(i, text='Q', answer='x' * 3997, status='answered') for i in range(10)]
        # Exactly 40,000 retained free-text characters.
        self.assertEqual(sum(len(q['text']) + len(q['answer']) for q in qs), 40000)
        self.seed(qs)
        self.llm.refine_sections = Mock(return_value=dict(proposals=[]))
        self.refine(self.read(), [])
        before = self.store.get(self.sid)
        target = qs[0]
        with self.assertRaises(RefinementLimitError):
            self.refine(self.read(), [dict(question_id=target['id'], status='answered', answer=target['answer'] + 'x')])
        self.assertEqual(self.store.get(self.sid), before)
        self.assertEqual(self.llm.refine_sections.call_count, 1)

    def test_resolutions_and_section_decisions_preserved_at_limit(self):
        qs = [question(i) for i in range(40)]
        qs[0].update(status='answered', answer='Existing answer')
        qs[1]['status'] = 'left_to_implementation'
        self.seed(qs)
        with self.store.operation(self.sid) as context:
            context.refinement['protected_sections'] = ['summary']
        result = self.refine(self.read(), [dict(question_id=qs[2]['id'], status='not_applicable', answer='')])
        self.assertEqual(result['question_history'][0]['answer'], 'Existing answer')
        self.assertEqual(result['question_history'][1]['status'], 'left_to_implementation')
        self.assertEqual(result['question_history'][2]['status'], 'not_applicable')
        self.assertEqual(result['protected_sections'], ['summary'])
        self.assertEqual(len(result['question_history']), 40)

    def test_concurrent_analyze_cannot_cross_limit(self):
        self.seed([question(i) for i in range(39)])
        self.llm.analyze_gaps = Mock(return_value=dict(questions=[{k: v for k, v in question(39).items() if k not in ('status', 'answer')}]))
        requests = [self.req() for _ in range(8)]
        barrier = Barrier(8)
        def mutate(request):
            barrier.wait(timeout=5)
            try:
                self.service.mutate(self.sid, 'analyze', request)
                return 'ok'
            except RevisionConflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(mutate, requests))
        self.assertEqual(results.count('ok'), 1)
        self.assertEqual(results.count('conflict'), 7)
        self.assertEqual(len(self.read()['question_history']), 40)

    def test_maximum_history_and_draft_have_usable_receipt(self):
        qs = [question(i, text='Q' * 996) for i in range(40)]
        self.seed(qs)
        maximum = dict(summary='x' * 20000, business_context='x' * 10000,
                       requirements='x' * 20000, acceptance_criteria='', open_questions='')
        request = self.req(DecisionsRequest, draft=maximum)
        # Synthetic receipt round trip, including normal draft envelope.
        state = self.store.get(self.sid).refinement
        token = review_receipt(self.sid, state['revision'], dict(history=qs, proposals=[]))
        self.assertLessEqual(len(token), REVIEW_TOKEN_CHARS)
        retry = request.model_dump()
        retry['review_token'] = token
        self.assertLessEqual(len(json.dumps(retry).encode()), BODY_BYTES)
        result = self.service.mutate(self.sid, 'decisions', DecisionsRequest(**retry))
        self.assertEqual(result['draft'], maximum)
        self.assertEqual(len(result['question_history']), 40)

    def test_generated_proposals_exceeding_receipt_limit_roll_back_answers(self):
        qs = [question(0, answer='Existing requirement', status='answered'),
              {**question(1, answer='Existing goal', status='answered'), 'section_ids': ['business_context']}]
        self.seed(qs)
        self.llm.refine_sections = Mock(return_value=dict(proposals=[
            dict(section_id=section, replacement='😀' * 20000, reason='Literal synthetic replacement',
                 question_ids=[q['id']], conflict=False)
            for section, q in zip(('requirements', 'business_context'), qs)]))
        before = self.store.get(self.sid)
        with self.assertRaises(RefinementLimitError):
            self.refine(self.read(), [dict(question_id='q-0', status='answered', answer='Updated answer')])
        self.assertEqual(self.store.get(self.sid), before)
        self.llm.refine_sections.assert_called_once()

    def test_maximum_history_with_proposals_receipt_round_trip(self):
        qs = [question(i, text='Q' * 996) for i in range(40)]
        qs[0].update(status='answered', answer='yes')
        self.seed(qs)
        self.llm.refine_sections = Mock(return_value=dict(proposals=[dict(
            section_id='requirements', replacement='x' * 20000,
            reason='Synthetic supported replacement', question_ids=['q-0'], conflict=False)]))
        state = self.refine(self.read(), [])
        token = review_receipt(self.sid, state['revision'], dict(
            history=state['question_history'], proposals=state['proposals']))
        self.assertLessEqual(len(token), REVIEW_TOKEN_CHARS)
        request = self.req(DecisionsRequest, review_token=token)
        result = self.service.mutate(self.sid, 'decisions', request)
        self.assertEqual(result['proposals'], state['proposals'])
        self.assertEqual(result['question_history'], state['question_history'])

    def test_worst_case_unicode_receipt_rejected_without_mutation(self):
        # Valid count/text budgets can exceed the receipt byte budget with Unicode.
        qs = [question(i, text='😀' * 996) for i in range(39)]
        self.seed(qs)
        before = self.store.get(self.sid)
        with self.assertRaises(RefinementLimitError):
            self.service.mutate(self.sid, 'decisions', self.req(DecisionsRequest))
        self.assertEqual(self.store.get(self.sid), before)

    def test_unusable_review_retry_rejected_without_commit(self):
        self.seed([question(i, text='Q' * 996) for i in range(40)])
        maximum = dict(summary='界' * 19900 + ' password=synthetic-secret', business_context='界' * 10000,
                       requirements='界' * 20000, acceptance_criteria='', open_questions='')
        before = self.store.get(self.sid)
        with self.assertRaises(RefinementLimitError):
            self.service.mutate(self.sid, 'decisions', self.req(DecisionsRequest, draft=maximum))
        self.assertEqual(self.store.get(self.sid), before)

    def test_review_answer_expansion_cannot_issue_invalid_retry(self):
        self.seed([question(i) for i in range(6)])
        # Six individually valid answers expand above the 20,000 aggregate
        # budget after mandatory secret redaction, but remain below 4,000 each.
        answer = 'x ' * 1630 + ' password=synthetic-one password=synthetic-two'
        request = self.req(RefineRequest, answers=[dict(
            question_id=f'q-{i}', status='answered', answer=answer) for i in range(6)])
        before = self.store.get(self.sid)
        with self.assertRaises(RefinementLimitError):
            self.service.mutate(self.sid, 'refine', request)
        self.assertEqual(self.store.get(self.sid), before)

    def test_actual_review_receipt_round_trip_preserves_history(self):
        self.seed([question(i, text='Q' * 996) for i in range(40)])
        request = self.req(DecisionsRequest, draft={**self.read()['draft'], 'summary': 'password=synthetic-secret'})
        before = self.store.get(self.sid)
        with self.assertRaises(ReviewNeeded) as review:
            self.service.mutate(self.sid, 'decisions', request)
        self.assertEqual(self.store.get(self.sid), before)
        body = request.model_dump()
        body.update(draft=review.exception.review.draft, review_token=review.exception.review.review_token)
        result = self.service.mutate(self.sid, 'decisions', DecisionsRequest(**body))
        self.assertEqual(len(result['question_history']), 40)
        self.assertNotIn('synthetic-secret', json.dumps(result))


class JiraCapacityTests(unittest.TestCase):
    def test_capacity_and_concurrent_last_slot(self):
        adapter = JiraAdapter()
        template = get_active_template()
        for _ in range(99):
            adapter.create_ticket(draft(), template)
        before = list(adapter._tickets)
        barrier = Barrier(16)
        def submit(_):
            barrier.wait(timeout=5)
            try:
                return adapter.create_ticket(draft(), template)
            except CapacityError:
                return None
        with ThreadPoolExecutor(max_workers=16) as pool:
            tickets = list(pool.map(submit, range(16)))
        self.assertEqual(sum(t is not None for t in tickets), 1)
        self.assertEqual(len(adapter._tickets), 100)
        self.assertEqual(adapter._tickets[:99], before)
        self.assertEqual(adapter._tickets[-1].key, 'MOCK-100')
        with self.assertRaises(CapacityError):
            adapter.create_ticket(draft(), template)
        self.assertEqual(len(adapter._tickets), 100)

    def test_rejection_preserves_submission_and_retry_is_single_use(self):
        store = SessionStore()
        adapter = JiraAdapter()
        o = Orchestrator(Masker(), store, LLMClient(), Validator(), adapter)
        sid, text = o.mask_preview('Synthetic requirement')
        generated = o.generate_draft(sid, text)
        for _ in range(99):
            adapter.create_ticket(draft(), get_active_template())
        before = store.get(sid)
        args = dict(expected_revision=generated.revision, request_id='submit')
        # Simulate a temporarily smaller capacity to exercise recovery without
        # deleting retained tickets or exceeding the real 100-ticket ceiling.
        with patch.object(adapter, '_max_tickets', 99):
            for _ in range(2):
                with self.assertRaises(CapacityError):
                    o.create_ticket(sid, draft(), reference(), **args)
                self.assertEqual(store.get(sid), before)
                self.assertEqual(len(adapter._tickets), 99)
        barrier = Barrier(8)
        def retry(_):
            barrier.wait(timeout=5)
            try:
                return o.create_ticket(sid, draft(), reference(), **args)
            except SessionUnavailableError:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(retry, range(8)))
        self.assertEqual(sum(t is not None for t in results), 1)
        self.assertEqual(len(adapter._tickets), 100)
        self.assertIsNone(store.get(sid))


class LimitsAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = SessionStore()
        self.llm = Mock(wraps=LLMClient())
        self.adapter = JiraAdapter()
        self.o = Orchestrator(Masker(), self.store, self.llm, Validator(), self.adapter)
        self.override = patch('app.main.orchestrator', self.o)
        self.override.start()
        self.client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), base_url='http://synthetic.test')

    async def asyncTearDown(self):
        await self.client.aclose()
        self.override.stop()

    async def test_real_body_boundaries_before_processing(self):
        base = json.dumps(dict(text='Synthetic input', enabled_rules=[])).encode()
        for size, status in [(BODY_BYTES - 1, 200), (BODY_BYTES, 200), (BODY_BYTES + 1, 413)]:
            before = len(self.store._sessions)
            response = await self.client.post('/api/mask-preview', content=base + b' ' * (size - len(base)),
                                              headers={'Content-Type': 'application/json'})
            self.assertEqual(response.status_code, status)
            self.assertEqual(len(self.store._sessions), before + (status == 200))
        self.llm.generate_draft.assert_not_called()

    async def test_oversized_generation_rejected_before_session_admission(self):
        sid, text = self.o.mask_preview('Synthetic')
        before = self.store.get(sid)
        base = json.dumps(dict(session_id=sid, masked_text=text)).encode()
        with patch.object(self.o, 'admit_session', wraps=self.o.admit_session) as admit:
            response = await self.client.post('/api/generate-draft',
                content=base + b' ' * BODY_BYTES, headers={'Content-Type': 'application/json'})
            self.assertEqual(response.status_code, 413)
            admit.assert_not_called()
        self.assertEqual(self.store.get(sid), before)
        self.llm.generate_draft.assert_not_called()

    async def test_masking_expansion_rejected_before_session_creation(self):
        # Many short, distinct emails expand to UUID placeholders.
        text = ' '.join(f'x{i}@a.co' for i in range(1800))
        self.assertLessEqual(len(text), 20000)
        response = await self.client.post('/api/mask-preview', json=dict(text=text))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(len(self.store._sessions), 0)

    async def test_chunked_body_without_content_length_and_cors(self):
        async def chunks(size):
            yield b'{"text":"Synthetic"}'
            yield b' ' * size
        for size, status in [(100, 200), (BODY_BYTES, 413)]:
            response = await self.client.post('/api/mask-preview', content=chunks(size),
                headers={'Origin': 'http://localhost:5173', 'Content-Type': 'application/json'})
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.headers['access-control-allow-origin'], 'http://localhost:5173')
            self.assertNotIn('content-length', response.request.headers)
            self.assertEqual(response.request.headers['transfer-encoding'], 'chunked')

    async def test_safe_schema_rejections_before_masking_and_llm(self):
        cases = [('/api/mask-preview', dict(text='password=synthetic-secret' + 'x' * 20000)),
                 ('/api/generate-draft', dict(session_id='A' * 22, masked_text='password=synthetic-secret' + 'x' * 40000)),
                 ('/api/create-ticket', dict(session_id='A' * 22, template=reference().model_dump(),
                                            draft={'summary': 'password=synthetic-secret' + 'x' * 20000})),
                 ('/api/clarifications/refine', dict(session_id='A' * 22, template=reference().model_dump(),
                    expected_revision=1, request_id='synthetic', draft=draft(),
                    answers=[dict(question_id='q', status='answered', answer='password=synthetic-secret' + 'x' * 4000)]))]
        with patch.object(self.o._masker, 'sanitize_fields', wraps=self.o._masker.sanitize_fields) as mask:
            for route, body in cases:
                response = await self.client.post(route, json=body)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json(), {'detail': {'code': 'INVALID_REQUEST', 'message': 'Invalid request'}})
                self.assertNotIn('synthetic-secret', response.text)
            mask.assert_not_called()
        self.assertEqual(len(self.store._sessions), 0)
        self.llm.generate_draft.assert_not_called()

    async def test_session_capacity_safe_503(self):
        for _ in range(100):
            self.store.create(Masker().context())
        response = await self.client.post('/api/mask-preview', json=dict(text='password=synthetic-secret'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['detail']['code'], 'CAPACITY_EXHAUSTED')
        self.assertNotIn('synthetic-secret', response.text)
        self.assertEqual(len(self.store._sessions), 100)

    async def test_ticket_full_503_preserves_session_revision_and_ttl(self):
        for _ in range(100):
            self.adapter.create_ticket(draft(), get_active_template())
        sid, text = self.o.mask_preview('Synthetic')
        generated = self.o.generate_draft(sid, text)
        before = self.store.get(sid)
        expiry = self.store._sessions[sid].expires_at
        body = dict(session_id=sid, template=reference().model_dump(), draft=draft(),
                    expected_revision=generated.revision, request_id='submit')
        for _ in range(2):
            response = await self.client.post('/api/create-ticket', json=body)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()['detail']['code'], 'CAPACITY_EXHAUSTED')
            self.assertEqual(self.store.get(sid), before)
            self.assertEqual(self.store._sessions[sid].expires_at, expiry)
            self.assertEqual(len(self.adapter._tickets), 100)

    async def test_history_full_safe_409(self):
        sid, text = self.o.mask_preview('Synthetic')
        self.o.generate_draft(sid, text)
        with self.store.operation(sid) as context:
            context.refinement['ledger'] = [question(i) for i in range(40)]
        state = self.store.get(sid).refinement
        response = await self.client.post('/api/clarifications/analyze', json=dict(session_id=sid,
            template=reference().model_dump(), expected_revision=state['revision'], request_id='analyze', draft=state['draft']))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail']['code'], 'REFINEMENT_LIMIT_REACHED')
        self.llm.analyze_gaps.assert_not_called()
        self.assertEqual(self.store.get(sid).refinement, state)


if __name__ == '__main__':
    unittest.main()
