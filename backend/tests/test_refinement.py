"""Synthetic Task 5 checks; no live provider requests."""
from protection_fixture import isolate_protection
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import httpx2
from openai import OpenAI
from pydantic import ValidationError
from app import main
from app.jira_adapter import JiraAdapter
from app.llm_client import LLMClient, OpenAIRequirementsGenerator
from app.masking import Masker, SanitizedText
from app.openai_client import LLMError, LLMTimeoutError, OpenAIClient
from app.orchestrator import Orchestrator
from app.refinement import RefinementService, RevisionConflict, ReviewNeeded
from app.refinement_schemas import AnalyzeRequest, RefineRequest, DecisionsRequest, QuestionBatch, ProposalBatch, Checkpoint, RefinementReview
from app.session_store import SessionStore, SessionUnavailableError
from app.validation import InvalidDraftError, Validator
from test_sessions import FakeClock, reference


class RefinementTests(unittest.TestCase):
    def setUp(self):
        isolate_protection(self)
        self.clock = FakeClock()
        self.store = SessionStore(clock=self.clock)
        self.llm = LLMClient()
        self.o = Orchestrator(Masker(), self.store, self.llm, Validator(), JiraAdapter())
        self.sid, text = self.o.mask_preview('Synthetic feature', [])
        self.generated = self.o.generate_draft(self.sid, text)
        self.service = RefinementService(self.o)
        self.counter = 0

    def read(self):
        return self.service.read(self.sid)

    def req(self, cls=AnalyzeRequest, state=None, **extra):
        state = state or self.read()
        self.counter += 1
        body = dict(session_id=self.sid, template=reference().model_dump(),
                    expected_revision=state['revision'], request_id=f'request-{self.counter}', draft=state['draft'])
        body.update(extra)
        return cls.model_validate(body)

    def analyze(self, **extra):
        return self.service.mutate(self.sid, 'analyze', self.req(**extra))

    def refine(self, state, answers):
        return self.service.mutate(self.sid, 'refine', self.req(RefineRequest, state, answers=answers))

    def decide(self, state, proposal, action):
        return self.service.mutate(self.sid, 'decisions', self.req(DecisionsRequest, state,
            batch_id=state['batch_id'], decisions=[dict(proposal_id=proposal['id'], action=action)]))

    def test_stub_partial_answers_no_automatic_apply_and_independent_decisions(self):
        state = self.analyze()
        self.assertEqual(len(state['questions']), 2)
        answers = [dict(question_id=q['id'], status='answered', answer='Literal ' + q['section_ids'][0])
                   for q in state['questions']]
        refined = self.refine(state, answers)
        self.assertEqual(refined['draft'], state['draft'])
        self.assertEqual(len(refined['proposals']), 2)
        accepted = self.decide(refined, refined['proposals'][0], 'accept')
        self.assertEqual(len(accepted['proposals']), 1)
        p = accepted['proposals'][0]
        rejected = self.decide(accepted, p, 'reject')
        self.assertEqual(rejected['draft'][p['section_id']], '')
        self.assertEqual(rejected['protected_sections'], [p['section_id']])
        again = self.refine(rejected, [])
        self.assertEqual(again['proposals'], [])
        enabled = self.service.mutate(self.sid, 'decisions', self.req(DecisionsRequest, again,
            allow_sections=[p['section_id']]))
        again = self.refine(enabled, [])
        self.assertEqual(len(again['proposals']), 1)
        self.assertEqual(again['proposals'][0]['section_id'], p['section_id'])

    def test_partial_omissions_and_all_dispositions_remain_traceable(self):
        for status in ['unanswered', 'answered', 'skipped', 'not_applicable',
                       'left_to_implementation', 'needs_stakeholder_decision']:
            with self.subTest(status=status):
                state = self.read()
                if not state['questions']:
                    state = self.analyze()
                q = state['questions'][0]
                state = self.refine(state, [dict(question_id=q['id'], status=status,
                    answer='Literal business goal' if status == 'answered' else '')])
                self.assertEqual(state['questions'][0]['status'], status)
                self.assertEqual(state['questions'][1]['status'], 'unanswered')
                items = '\n'.join(state['open_question_items'])
                self.assertEqual(q['text'] in items, status not in ['answered', 'not_applicable'])
                if status == 'left_to_implementation':
                    self.assertIn('Решение делегировано реализации', items)

    def test_normalized_duplicate_and_resolved_suppression(self):
        state = self.analyze()
        q = state['questions'][0]
        state = self.refine(state, [dict(question_id=q['id'], status='not_applicable', answer='')])
        provider = {k: q[k] for k in ['id', 'text', 'section_ids', 'priority', 'rationale', 'evidence']}
        provider['id'] = 'provider1'
        duplicate = {**provider, 'id': 'provider2', 'text': provider['text'].upper() + ' !!!'}
        with patch.object(self.llm, 'analyze_gaps', return_value=dict(questions=[provider, duplicate])):
            state = self.analyze()
        self.assertEqual(state['questions'], [])
        self.assertEqual(len(state['question_history']), 2)

    def test_current_manual_draft_is_used_and_unrelated_text_preserved(self):
        state = self.analyze()
        draft = {**state['draft'], 'requirements': 'Manual authoritative requirement'}
        state = self.service.mutate(self.sid, 'analyze', self.req(state=state, draft=draft))
        q = state['questions'][0]
        state = self.refine(state, [dict(question_id=q['id'], status='answered', answer='Explicit goal')])
        state = self.decide(state, state['proposals'][0], 'accept')
        state = self.analyze()
        self.assertEqual(state['draft']['requirements'], draft['requirements'])
        self.assertEqual(state['draft']['business_context'], 'Explicit goal')

    def test_stale_revision_replay_and_reused_id_are_safe(self):
        request = self.req()
        first = self.service.mutate(self.sid, 'analyze', request)
        again = self.service.mutate(self.sid, 'analyze', request)
        self.assertEqual(first, again)
        self.assertEqual(self.read()['revision'], 2)
        with self.assertRaises(RevisionConflict):
            self.service.mutate(self.sid, 'analyze', request.model_copy(update={'draft': {**request.draft, 'summary': 'Changed'}}))
        with self.assertRaises(RevisionConflict):
            self.service.mutate(self.sid, 'analyze', self.req(expected_revision=1))

    def test_replay_metadata_bounded_and_contains_no_raw_request(self):
        for _ in range(35):
            self.analyze()
        state = self.store.get(self.sid).refinement
        self.assertEqual(len(state['replay']), 32)
        self.assertTrue(all(len(digest) == 64 for digest in state['replay'].values()))

    def test_sanitization_requires_review_without_provider_or_commit(self):
        before = self.read()
        with patch.object(self.llm, 'analyze_gaps', wraps=self.llm.analyze_gaps) as provider:
            with self.assertRaises(ReviewNeeded) as review:
                self.analyze(draft={**before['draft'], 'requirements': 'password=synthetic-secret',
                                    'summary': 'synthetic-secret'})
            provider.assert_not_called()
        self.assertNotIn('synthetic-secret', str(review.exception.review.model_dump()))
        self.assertEqual(before, self.read())
        approved = self.analyze(draft=review.exception.review.draft)
        self.assertEqual(approved['revision'], 2)

    def test_answers_and_draft_are_sanitized_together_before_refine(self):
        state = self.analyze()
        q = state['questions'][0]
        request = self.req(RefineRequest, state, answers=[dict(question_id=q['id'], status='answered',
            answer='password=synthetic-answer')], draft={**state['draft'], 'summary': 'synthetic-answer'})
        with patch.object(self.llm, 'refine_sections') as provider:
            with self.assertRaises(ReviewNeeded) as review:
                self.service.mutate(self.sid, 'refine', request)
            provider.assert_not_called()
        safe = review.exception.review
        self.assertNotIn('synthetic-answer', str(safe.model_dump()))
        self.assertEqual(state, self.read())

    def test_generated_questions_and_proposals_sanitized(self):
        q = dict(id='q', text='What is the goal?', section_ids=['business_context'],
                 priority='recommended', rationale='password=synthetic-output', evidence='')
        with patch.object(self.llm, 'analyze_gaps', return_value=dict(questions=[q])):
            state = self.analyze()
        self.assertNotIn('synthetic-output', str(state))
        qid = state['questions'][0]['id']
        proposal = dict(section_id='business_context', replacement='token=synthetic-proposal',
                        reason='Literal answer', question_ids=[qid], conflict=True)
        with patch.object(self.llm, 'refine_sections', return_value=dict(proposals=[proposal])):
            state = self.refine(state, [dict(question_id=qid, status='answered', answer='Explicit goal')])
        self.assertNotIn('synthetic-proposal', str(state))
        self.assertTrue(state['proposals'][0]['conflict'])

    def test_zero_to_five_questions_and_invalid_references(self):
        for count in range(6):
            qs = [dict(id=f'q{i}', text=f'Question {i}?', section_ids=['requirements'],
                       priority='optional', rationale='Review explicit requirement', evidence='Synthetic feature')
                  for i in range(count)]
            with patch.object(self.llm, 'analyze_gaps', return_value=dict(questions=qs)):
                self.assertLessEqual(len(self.analyze()['questions']), count)
        before = self.read()
        for change in [dict(section_ids=['unknown']), dict(priority='unknown'), dict(id='invalid id')]:
            q = {**qs[0], **change}
            with patch.object(self.llm, 'analyze_gaps', return_value=dict(questions=[q])):
                with self.assertRaises(LLMError):
                    self.analyze()
            self.assertEqual(before, self.read())
        with patch.object(self.llm, 'analyze_gaps', return_value=dict(questions=qs + [dict(qs[0], id='six')])):
            with self.assertRaises(LLMError):
                self.analyze()

    def test_invalid_answers_and_dispositions_rollback(self):
        state = self.analyze()
        qid = state['questions'][0]['id']
        for answer in [dict(question_id='unknown', status='answered', answer='Literal'),
                       dict(question_id=qid, status='answered', answer=''),
                       dict(question_id=qid, status='skipped', answer='Invented')]:
            with self.assertRaises(InvalidDraftError):
                self.refine(state, [answer])
            self.assertEqual(state, self.read())
        with self.assertRaises(ValidationError):
            self.req(RefineRequest, state, answers=[dict(question_id=qid, status='invented', answer='')])

    def test_gap_evidence_with_real_sdk_parser_and_mock_http(self):
        question = dict(id='q1', text='Who may export?', section_ids=['requirements'],
                        priority='recommended', rationale='Permissions are unspecified', evidence='Export a CSV')
        cases = [
            ('exact', {}, 'Export a CSV', None),
            ('paraphrased', dict(evidence='Export CSV'), '', None),
            ('wrong_section', dict(section_ids=['business_context']), '', None),
            ('invalid_section', dict(section_ids=['unknown']), None, 'structured_output'),
            ('invalid_priority', dict(priority='unknown'), None, 'structured_output'),
            ('invalid_id', dict(id='invalid id'), None, 'structured_output'),
        ]
        for name, overrides, evidence, category in cases:
            with self.subTest(name=name):
                calls = []
                def transport(request):
                    calls.append(json.loads(request.content))
                    return httpx2.Response(200, json={
                        'id': 'resp_synthetic', 'object': 'response', 'created_at': 0,
                        'status': 'completed', 'model': 'synthetic-model', 'output': [{
                            'id': 'msg_synthetic', 'type': 'message', 'status': 'completed',
                            'role': 'assistant', 'content': [{
                                'type': 'output_text', 'annotations': [],
                                'text': json.dumps(dict(questions=[{**question, **overrides}])),
                            }],
                        }],
                    })
                o = Orchestrator(Masker(), SessionStore(), LLMClient(), Validator(), JiraAdapter())
                sid, text = o.mask_preview('Export a CSV', [])
                generated = o.generate_draft(sid, text)
                service = RefinementService(o)
                before = service.read(sid)
                request = AnalyzeRequest(session_id=sid, template=generated.template,
                    expected_revision=1, request_id='synthetic-analysis', draft=generated.draft)
                with OpenAI(api_key='synthetic-not-a-real-key', max_retries=0,
                            http_client=httpx2.Client(transport=httpx2.MockTransport(transport))) as sdk:
                    provider = OpenAIRequirementsGenerator()
                    provider._client._client = sdk
                    o._llm_client = provider
                    if category:
                        with self.assertRaises(LLMError) as error:
                            service.mutate(sid, 'analyze', request)
                        self.assertEqual(error.exception.category, category)
                        self.assertEqual(service.read(sid), before)
                    else:
                        result = service.mutate(sid, 'analyze', request)
                        self.assertEqual(result['questions'][0]['evidence'], evidence)
                        self.assertEqual(result['revision'], 2)
                        self.assertEqual(result['draft'], generated.draft)
                self.assertEqual(len(calls), 1)
                self.assertTrue(calls[0]['text']['format']['strict'])
                self.assertFalse(calls[0]['store'])

    def test_failure_classification_preserves_safe_errors(self):
        client = OpenAIClient()
        client._client = Mock()
        client._client.responses.parse.side_effect = RuntimeError('password=synthetic-private')
        with self.assertRaises(LLMError) as error:
            client.ask(SanitizedText('Static instructions'), SanitizedText('{"draft": {}}'), QuestionBatch)
        self.assertEqual(error.exception.category, 'provider_api')
        self.assertNotIn('synthetic-private', str(error.exception))
        self.assertIsNone(error.exception.__context__)

        question = dict(id='duplicate', text='What goal?', section_ids=['business_context'],
                        priority='recommended', rationale='Missing goal', evidence='')
        with patch.object(self.llm, 'analyze_gaps', return_value=dict(questions=[
                question, {**question, 'text': 'What outcome?'}])):
            with self.assertRaises(LLMError) as error:
                self.analyze()
        self.assertEqual(error.exception.category, 'local_validation')

    def test_stale_section_base_rejects_decision_and_rolls_back_sync(self):
        state = self.analyze()
        q = state['questions'][0]
        state = self.refine(state, [dict(question_id=q['id'], status='answered', answer='Literal goal')])
        p = state['proposals'][0]
        request = self.req(DecisionsRequest, state, draft={**state['draft'], p['section_id']: 'New manual edit'},
            batch_id=state['batch_id'], decisions=[dict(proposal_id=p['id'], action='accept')])
        with self.assertRaises(RevisionConflict):
            self.service.mutate(self.sid, 'decisions', request)
        self.assertEqual(state, self.read())

    def test_provider_cannot_propose_protected_or_unrelated_sections(self):
        state = self.analyze()
        q = state['questions'][0]
        state = self.refine(state, [dict(question_id=q['id'], status='answered', answer='Literal goal')])
        p = state['proposals'][0]
        state = self.decide(state, p, 'reject')
        raw = {k: p[k] for k in ['section_id', 'replacement', 'reason', 'question_ids', 'conflict']}
        for section in [p['section_id'], 'requirements']:
            with patch.object(self.llm, 'refine_sections', return_value=dict(proposals=[{**raw, 'section_id': section}])):
                result = self.refine(state, [])
            self.assertEqual(result['proposals'], [])
            self.assertEqual(result['draft'], state['draft'])
            self.assertEqual(result['protected_sections'], state['protected_sections'])
            state = result

    def test_partial_filtering_preserves_valid_proposals_answers_and_dispositions(self):
        state = self.analyze()
        goal, criteria = state['questions']
        answers = [dict(question_id=goal['id'], status='answered', answer='Explicit goal'),
                   dict(question_id=criteria['id'], status='needs_stakeholder_decision', answer='')]
        valid = dict(section_id='business_context', replacement='Explicit goal',
                     reason='Literal answer', question_ids=[goal['id']], conflict=True)
        invalid = dict(section_id='acceptance_criteria', replacement='Unsupported text',
                       reason='Synthetic reason', question_ids=['unknown'], conflict=False)
        request = self.req(RefineRequest, state, answers=answers)
        with patch.object(self.llm, 'refine_sections', return_value=dict(proposals=[invalid, valid])) as provider:
            with self.assertLogs('app.refinement', level='WARNING') as logs:
                result = self.service.mutate(self.sid, 'refine', request)
            replay = self.service.mutate(self.sid, 'refine', request)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(replay, result)
        self.assertEqual(logs.output, ['WARNING:app.refinement:Refinement proposals excluded category=unsupported_reference count=1'])
        self.assertIsNone(logs.records[0].exc_info)
        self.assertEqual(len(result['proposals']), 1)
        self.assertEqual(result['proposals'][0]['replacement'], 'Explicit goal')
        self.assertTrue(result['proposals'][0]['conflict'])
        self.assertEqual(result['draft'], state['draft'])
        self.assertEqual(result['revision'], state['revision'] + 1)
        self.assertEqual(result['base_revision'], result['revision'])
        self.assertEqual(result, self.read())
        for answer in answers:
            saved = next(q for q in result['question_history'] if q['id'] == answer['question_id'])
            self.assertEqual((saved['status'], saved['answer']), (answer['status'], answer['answer']))

    def test_duplicate_sections_discard_all_candidates_regardless_of_order(self):
        state = self.analyze()
        goal, criteria = state['questions']
        answers = [dict(question_id=q['id'], status='answered', answer='Explicit ' + q['id'])
                   for q in state['questions']]
        first = dict(section_id='business_context', replacement='First goal', reason='Synthetic',
                     question_ids=[goal['id']], conflict=False)
        second = {**first, 'replacement': 'Other goal', 'question_ids': ['unknown']}
        valid = {**first, 'section_id': 'acceptance_criteria', 'replacement': 'Explicit result',
                 'question_ids': [criteria['id']]}
        for batch in ([first, valid, second], [second, valid, first]):
            with patch.object(self.llm, 'refine_sections', return_value=dict(proposals=batch)):
                state = self.refine(state, answers)
            self.assertEqual([p['section_id'] for p in state['proposals']], ['acceptance_criteria'])

    def test_unsupported_references_produce_empty_batch_and_keep_answers(self):
        state = self.analyze()
        goal, criteria = state['questions']
        answers = [dict(question_id=goal['id'], status='answered', answer='Explicit goal')]
        proposal = dict(section_id='business_context', replacement='Synthetic replacement', reason='Synthetic',
                        question_ids=[goal['id']], conflict=False)
        for references in (['unknown'], [goal['id'], 'unknown'], [criteria['id']]):
            with patch.object(self.llm, 'refine_sections', return_value=dict(proposals=[
                    {**proposal, 'question_ids': references}])):
                state = self.refine(state, answers)
            self.assertEqual(state['proposals'], [])
            self.assertEqual(state['questions'][0]['answer'], 'Explicit goal')
            self.assertEqual(state, self.read())

    def test_malformed_proposal_batches_still_fail_and_rollback_answers(self):
        state = self.analyze()
        q = state['questions'][0]
        proposal = dict(section_id='business_context', replacement='Literal goal', reason='Synthetic',
                        question_ids=[q['id']], conflict=False)
        for raw in ({}, {'proposals': None}, {'proposals': [], 'extra': 'forbidden'},
                    {'proposals': [{**proposal, 'conflict': 'false'}]},
                    {'proposals': [{**proposal, 'section_id': 'unknown'}]},
                    {'proposals': [{**proposal, 'question_ids': ['invalid id']}]},
                    {'proposals': [proposal] * 6}):
            with self.subTest(raw=raw), patch.object(self.llm, 'refine_sections', return_value=raw):
                with self.assertRaises(LLMError) as error:
                    self.refine(state, [dict(question_id=q['id'], status='answered', answer='Explicit goal')])
                self.assertEqual(error.exception.category, 'structured_output')
                self.assertEqual(state, self.read())

    def test_failures_reads_and_expiry_do_not_refresh_ttl(self):
        self.clock.set(self.clock() + 899)
        self.read()
        with patch.object(self.llm, 'analyze_gaps', side_effect=LLMTimeoutError('Safe')):
            with self.assertRaises(LLMTimeoutError):
                self.analyze()
        self.clock.set(self.clock() + 1)
        with self.assertRaises(SessionUnavailableError):
            self.read()

    def test_success_refreshes_ttl_and_failed_worker_rolls_back(self):
        self.clock.set(self.clock() + 899)
        state = self.analyze()
        self.clock.set(self.clock() + 899)
        self.assertEqual(state, self.read())
        with patch.object(self.llm, 'refine_sections', side_effect=LLMError('Safe')):
            with self.assertRaises(LLMError):
                self.refine(state, [])
        self.assertEqual(state, self.read())

    def test_running_worker_recovers_after_reservation_cancellation(self):
        entered, finish = Event(), Event()
        def delayed(payload):
            entered.set()
            self.assertTrue(finish.wait(3))
            return dict(questions=[])
        request = self.req()
        reservation = self.o.admit_session(self.sid)
        with patch.object(self.llm, 'analyze_gaps', side_effect=delayed), ThreadPoolExecutor() as pool:
            worker = pool.submit(self.service.mutate, self.sid, 'analyze', request, reservation=reservation)
            self.assertTrue(entered.wait(3))
            reservation.cancel()
            self.clock.set(self.clock() + 901)
            self.assertEqual(self.store.cleanup_expired(), 0)
            finish.set()
            result = worker.result(3)
        self.assertEqual(result, self.read())
        self.assertEqual(self.service.mutate(self.sid, 'analyze', request), result)

    def test_final_revision_sync_required_fields_and_consumption(self):
        state = self.analyze()
        complete = {**state['draft'], 'business_context': 'Manual goal', 'acceptance_criteria': 'Observable result'}
        with self.assertRaises(RevisionConflict):
            self.o.create_ticket(self.sid, complete, reference(), expected_revision=1, request_id='final')
        ticket = self.o.create_ticket(self.sid, complete, reference(), expected_revision=state['revision'], request_id='final')
        self.assertIn('Manual goal', ticket.description)
        with self.assertRaises(SessionUnavailableError):
            self.o.create_ticket(self.sid, complete, reference(), expected_revision=state['revision'], request_id='final')

    def test_mocked_openai_uses_existing_structured_client(self):
        generator = OpenAIRequirementsGenerator()
        generator._client = Mock()
        generator._client.ask.side_effect = [QuestionBatch(questions=[]), ProposalBatch(proposals=[])]
        payload = SanitizedText('{"draft": {}}')
        self.assertEqual(generator.analyze_gaps(payload), dict(questions=[]))
        self.assertEqual(generator.refine_sections(payload), dict(proposals=[]))
        self.assertEqual(generator._client.ask.call_args_list[0].args[2], QuestionBatch)
        self.assertEqual(generator._client.ask.call_args_list[1].args[2], ProposalBatch)

    def test_sdk_recheck_masks_content_without_changing_structural_references(self):
        client = OpenAIClient()
        client._client = Mock()
        client._client.responses.parse.return_value = SimpleNamespace(
            status='completed', output=[], output_parsed=ProposalBatch(proposals=[]))
        qid = 'sk-' + 'a1' * 16  # Valid structural ID that also resembles a token.
        question = dict(id=qid, text='Clarify the goal?', section_ids=['business_context'],
            priority='recommended', rationale='Synthetic rationale', evidence='',
            status='answered', answer='password=synthetic-boundary-value')
        payload = dict(draft=self.generated.draft, questions=[question],
            eligible_sections=['business_context'], protected_sections=[])
        payload['draft']['business_context'] = 'synthetic-boundary-value'
        client.ask(SanitizedText('Static instructions'),
                   SanitizedText(json.dumps(payload)), ProposalBatch)
        sent = json.loads(client._client.responses.parse.call_args.kwargs['input'][1]['content'])
        self.assertEqual(sent['questions'][0]['id'], qid)
        self.assertEqual(sent['questions'][0]['status'], 'answered')
        self.assertEqual(sent['questions'][0]['section_ids'], ['business_context'])
        self.assertNotIn('synthetic-boundary-value', sent['questions'][0]['answer'])
        self.assertNotIn('synthetic-boundary-value', sent['draft']['business_context'])

    def test_review_resubmission_does_not_leak_cross_history_secret(self):
        question = dict(id='goal', text='What about synthetic-history-value?', section_ids=['business_context'],
                        priority='recommended', rationale='Clarify the goal', evidence='')
        with patch.object(self.llm, 'analyze_gaps', return_value=dict(questions=[question])):
            state = self.analyze()
        qid = state['questions'][0]['id']
        with self.assertRaises(ReviewNeeded) as review:
            self.refine(state, [dict(question_id=qid, status='answered', answer='password=synthetic-history-value')])
        safe = review.exception.review
        self.assertNotIn('synthetic-history-value', str(safe.model_dump()))
        self.assertEqual(state, self.read())
        with patch.object(self.llm, 'refine_sections', return_value=dict(proposals=[])) as provider:
            request = self.req(RefineRequest, state, draft=safe.draft,
                answers=[a.model_dump() for a in safe.answers], review_token=safe.review_token)
            result = self.service.mutate(self.sid, 'refine', request)
        self.assertNotIn('synthetic-history-value', provider.call_args.args[0].text)
        self.assertNotIn('synthetic-history-value', str(result))

    def test_review_receipt_is_bound_to_session_revision_and_integrity(self):
        with self.assertRaises(ReviewNeeded) as review:
            self.analyze(draft={**self.read()['draft'], 'summary': 'password=synthetic-receipt'})
        safe = review.exception.review
        with self.assertRaises(InvalidDraftError):
            self.analyze(draft=safe.draft, review_token=safe.review_token + 'tampered')
        state = self.analyze()
        with self.assertRaises(InvalidDraftError):
            self.analyze(draft=safe.draft, review_token=safe.review_token)
        self.assertEqual(state, self.read())

    def test_new_evidence_reconsiders_dismissal_without_erasing_decision(self):
        state = self.analyze()
        q = state['questions'][0]
        state = self.refine(state, [dict(question_id=q['id'], status='not_applicable', answer='')])
        raw = {k: q[k] for k in ['id', 'text', 'section_ids', 'priority', 'rationale', 'evidence']}
        raw['evidence'] = 'New stakeholder motivation'
        draft = {**state['draft'], q['section_ids'][0]: raw['evidence']}
        with patch.object(self.llm, 'analyze_gaps', return_value=dict(questions=[raw])):
            state = self.analyze(draft=draft)
        self.assertEqual(len(state['questions']), 1)
        self.assertNotEqual(state['questions'][0]['id'], q['id'])
        prior = next(item for item in state['question_history'] if item['id'] == q['id'])
        self.assertEqual(prior['status'], 'not_applicable')
        self.assertEqual(prior['superseded_by'], state['questions'][0]['id'])

    def test_sdk_mocked_openai_analysis_and_refinement_end_to_end(self):
        generator = OpenAIRequirementsGenerator()
        sdk = Mock()
        generator._client._client = sdk
        self.o._llm_client = generator
        question = dict(id='goal', text='What goal?', section_ids=['business_context'],
                        priority='recommended', rationale='password=synthetic-generated', evidence='')
        sdk.responses.parse.return_value = SimpleNamespace(status='completed', output=[],
            output_parsed=QuestionBatch(questions=[question]))
        state = self.analyze()
        self.assertNotIn('synthetic-generated', str(state))
        qid = state['questions'][0]['id']
        sdk.responses.parse.return_value = SimpleNamespace(status='completed', output=[],
            output_parsed=ProposalBatch(proposals=[dict(section_id='business_context', replacement='Literal goal',
                reason='Explicit answer', question_ids=[qid], conflict=False)]))
        state = self.refine(state, [dict(question_id=qid, status='answered', answer='Literal goal')])
        self.assertEqual(state['draft']['business_context'], '')
        self.assertEqual(state['proposals'][0]['replacement'], 'Literal goal')
        for call in sdk.responses.parse.call_args_list:
            self.assertFalse(call.kwargs['store'])
            self.assertNotIn(self.sid, str(call.kwargs))
            self.assertNotIn('synthetic-generated', str(call.kwargs))
        self.assertIs(sdk.responses.parse.call_args_list[0].kwargs['text_format'], QuestionBatch)
        self.assertIs(sdk.responses.parse.call_args_list[1].kwargs['text_format'], ProposalBatch)

    def test_open_items_survive_manual_removal_and_preview_deduplicates(self):
        state = self.analyze()
        q = state['questions'][0]
        draft = {**state['draft'], 'open_questions': 'Manual note\n- ' + q['text']}
        state = self.service.mutate(self.sid, 'decisions', self.req(DecisionsRequest, state, draft=draft))
        self.assertIn(q['text'], state['open_question_items'])
        self.assertEqual(state['open_questions_preview'].count(q['text']), 1)

    def test_final_sanitization_review_preserves_session_and_manual_open_questions(self):
        state = self.analyze()
        draft = {**state['draft'], 'business_context': 'Manual goal', 'acceptance_criteria': 'Observable result',
                 'open_questions': 'Manual question password=synthetic-final'}
        with self.assertRaises(ReviewNeeded) as review:
            self.o.create_ticket(self.sid, draft, reference(), expected_revision=state['revision'], request_id='submit')
        self.assertEqual(state, self.read())
        self.assertNotIn('synthetic-final', str(review.exception.review.model_dump()))
        ticket = self.o.create_ticket(self.sid, review.exception.review.draft, reference(),
            expected_revision=state['revision'], request_id='submit')
        self.assertIn('Manual question', ticket.description)
        self.assertNotIn('synthetic-final', ticket.description)


class RefinementAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_diagnostics_log_only_fixed_categories(self):
        for category in ['provider_api', 'structured_output', 'local_validation']:
            with self.subTest(category=category), self.assertLogs('app.main', level='WARNING') as logs:
                response = await main.llm_error(None, LLMError('password=synthetic-private', category=category))
            self.assertEqual(logs.output, [f'WARNING:app.main:LLM failure category={category}'])
            self.assertIsNone(logs.records[0].exc_info)
            self.assertEqual(response.status_code, 502)
            self.assertEqual(json.loads(response.body)['detail']['code'], 'LLM_GENERATION_FAILED')
            self.assertNotIn('synthetic-private', response.body.decode())

    async def test_routes_safe_errors_review_and_recovery(self):
        store = SessionStore()
        o = Orchestrator(Masker(), store, LLMClient(), Validator(), JiraAdapter())
        sid, text = o.mask_preview('Synthetic feature', [])
        generated = o.generate_draft(sid, text)
        body = dict(session_id=sid, template=reference().model_dump(), expected_revision=1,
                    request_id='analyze', draft=generated.draft)
        with patch.object(main, 'orchestrator', o):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), base_url='http://test') as client:
                result = await client.post('/api/clarifications/analyze', json=body)
                self.assertEqual(result.status_code, 200)
                recovered = await client.get(f'/api/sessions/{sid}/refinement')
                self.assertEqual(result.json(), recovered.json())
                conflict = await client.post('/api/clarifications/analyze', json={**body, 'request_id': 'stale'})
                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(conflict.json()['detail']['code'], 'REVISION_CONFLICT')
                invalid = await client.post('/api/clarifications/refine', json={**body, 'raw_secret': 'synthetic'})
                self.assertEqual(invalid.status_code, 422)
                self.assertNotIn('synthetic', str(invalid.json()))
                with patch.object(o._llm_client, 'analyze_gaps', side_effect=LLMError('password=private-synthetic')):
                    failed = await client.post('/api/clarifications/analyze', json={**body, 'request_id': 'failed', 'expected_revision': 2})
                self.assertEqual(failed.status_code, 502)
                self.assertNotIn('private-synthetic', str(failed.json()))
                await client.delete(f'/api/sessions/{sid}')
                expired = await client.get(f'/api/sessions/{sid}/refinement')
                self.assertEqual(expired.status_code, 404)
                self.assertEqual(expired.json()['detail']['code'], 'SESSION_UNAVAILABLE')


class DefaultMaskingRefinementAPITests(unittest.IsolatedAsyncioTestCase):
    """Exercise real SDK parsing over mock HTTP, with every default mask enabled."""

    async def asyncSetUp(self):
        isolate_protection(self)
        self.o = Orchestrator(Masker(), SessionStore(), LLMClient(), Validator(), JiraAdapter())
        self.main_patch = patch.object(main, 'orchestrator', self.o)
        self.main_patch.start()
        self.client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), base_url='http://test')
        preview = (await self.client.post('/api/mask-preview', json={'text': 'Synthetic feature'})).json()
        self.sid = preview['session_id']
        self.generated = (await self.client.post('/api/generate-draft', json=preview)).json()
        self.assertEqual(self.generated['revision'], 1)
        self.assertIn('wallets', self.o._session_store.get(self.sid).enabled_keys)
        self.question_output = [dict(id=('a1' * 16 if i == 0 else f'q{i}'),
            text=f'Clarify section {i}?', section_ids=[section], priority='recommended',
            rationale='Synthetic rationale', evidence='')
            for i, section in enumerate(self.generated['draft'])]
        self.payloads = []
        self.proposal_changes = {}

        def transport(request):
            body = json.loads(request.content)
            payload = json.loads(body['input'][1]['content'])
            self.payloads.append(payload)
            if 'eligible_sections' in payload:
                output = dict(proposals=[dict(section_id=q['section_ids'][0],
                    replacement=f"Explicit replacement {i}", reason='Explicit answer',
                    question_ids=[q['id']], conflict=False) | self.proposal_changes
                    for i, q in enumerate(payload['questions'])
                    if q['status'] == 'answered' and q['section_ids'][0] in payload['eligible_sections']])
            else:
                output = dict(questions=self.question_output)
            return httpx2.Response(200, json={
                'id': 'resp_synthetic', 'object': 'response', 'created_at': 0,
                'status': 'completed', 'model': 'synthetic-model', 'output': [{
                    'id': 'msg_synthetic', 'type': 'message', 'status': 'completed',
                    'role': 'assistant', 'content': [{
                        'type': 'output_text', 'annotations': [], 'text': json.dumps(output),
                    }],
                }],
            })

        self.sdk = OpenAI(api_key='synthetic-not-a-real-key', max_retries=0,
            http_client=httpx2.Client(transport=httpx2.MockTransport(transport)))
        provider = OpenAIRequirementsGenerator()
        provider._client._client = self.sdk
        self.o._llm_client = provider
        self.counter = 0

    async def asyncTearDown(self):
        self.sdk.close()
        await self.client.aclose()
        self.main_patch.stop()

    def body(self, state, **extra):
        self.counter += 1
        return dict(session_id=self.sid, template=state['template'], draft=state['draft'],
            expected_revision=state['revision'], request_id=f'api-{self.counter}', **extra)

    async def analyze(self):
        response = await self.client.post('/api/clarifications/analyze', json=self.body(self.generated))
        self.assertEqual(response.status_code, 200)
        state = response.json()
        Checkpoint.model_validate(state)
        self.assertEqual(len(state['questions']), 5)
        return state

    def answers(self, state):
        return [dict(question_id=q['id'], status='answered', answer=f'Explicit answer {i}')
                for i, q in enumerate(state['questions'])]

    async def recovered(self):
        response = await self.client.get(f'/api/sessions/{self.sid}/refinement')
        self.assertEqual(response.status_code, 200)
        return response.json()

    async def test_five_answers_proposals_replay_decisions_and_recovery(self):
        state = await self.analyze()
        ids = [q['id'] for q in state['questions']]
        self.assertEqual(state['revision'], 2)
        body = self.body(state, answers=self.answers(state))
        response = await self.client.post('/api/clarifications/refine', json=body)
        self.assertEqual(response.status_code, 200)
        refined = response.json()
        Checkpoint.model_validate(refined)
        self.assertEqual(len(refined['proposals']), 5)
        self.assertEqual(refined['revision'], 3)
        self.assertEqual(refined['base_revision'], 3)
        self.assertEqual(refined['draft'], state['draft'])
        self.assertEqual([q['id'] for q in refined['questions']], ids)
        self.assertEqual([q['id'] for q in self.payloads[-1]['questions']], ids)
        self.assertEqual([p['question_ids'] for p in refined['proposals']], [[qid] for qid in ids])
        self.assertEqual(await self.recovered(), refined)
        replay = await self.client.post('/api/clarifications/refine', json=body)
        self.assertEqual(replay.json(), refined)
        self.assertEqual(len(self.payloads), 2)
        stale = await self.client.post('/api/clarifications/refine', json=self.body(state, answers=[]))
        self.assertEqual(stale.status_code, 409)
        p = refined['proposals'][0]
        response = await self.client.post('/api/clarifications/decisions', json=self.body(refined,
            batch_id=refined['batch_id'], decisions=[dict(proposal_id=p['id'], action='reject')]))
        self.assertEqual(response.status_code, 200)
        rejected = response.json()
        self.assertEqual(rejected['revision'], 4)
        self.assertEqual(rejected['protected_sections'], [p['section_id']])
        self.assertEqual(rejected['draft'], state['draft'])
        self.assertEqual(rejected['batch_id'], refined['batch_id'])
        self.assertEqual([x['id'] for x in rejected['proposals']], [x['id'] for x in refined['proposals'][1:]])
        with patch.object(self.o._llm_client, 'refine_sections', return_value=dict(proposals=[{
            k: p[k] for k in ('section_id', 'replacement', 'reason', 'question_ids', 'conflict')}])):
            response = await self.client.post('/api/clarifications/refine', json=self.body(rejected, answers=[]))
        self.assertEqual(response.status_code, 200)
        filtered = response.json()
        self.assertEqual(filtered['proposals'], [])
        self.assertEqual(filtered['protected_sections'], rejected['protected_sections'])
        self.assertEqual(filtered['draft'], rejected['draft'])
        self.assertEqual(await self.recovered(), filtered)

    async def test_sensitive_answer_review_preserves_ids_and_rolls_back(self):
        state = await self.analyze()
        answers = self.answers(state)
        answers[0]['answer'] = 'password=synthetic-review-value'
        answers[1]['answer'] = 'synthetic-review-value'
        response = await self.client.post('/api/clarifications/refine', json=self.body(state, answers=answers))
        self.assertEqual(response.status_code, 200)
        review = response.json()
        RefinementReview.model_validate(review)
        self.assertEqual(review['status'], 'preview_review_required')
        self.assertEqual([a['question_id'] for a in review['answers']], [a['question_id'] for a in answers])
        self.assertEqual([q['id'] for q in review['history']], [q['id'] for q in state['question_history']])
        self.assertNotIn('synthetic-review-value', str(review))
        self.assertEqual(len(self.payloads), 1)
        self.assertEqual(await self.recovered(), state)
        response = await self.client.post('/api/clarifications/refine', json=self.body(state,
            answers=review['answers'], review_token=review['review_token']))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['revision'], 3)
        self.assertNotIn('synthetic-review-value', str(self.payloads))
        self.assertNotIn('synthetic-review-value', str(await self.recovered()))

    async def test_provider_question_and_proposal_content_stays_masked(self):
        self.question_output[0].update(text='password=synthetic-question-value',
            rationale='synthetic-question-value', evidence='synthetic-question-value')
        state = await self.analyze()
        self.assertNotIn('synthetic-question-value', str(state))
        self.proposal_changes = dict(replacement='token=synthetic-proposal-value', reason='synthetic-proposal-value')
        response = await self.client.post('/api/clarifications/refine', json=self.body(state, answers=self.answers(state)))
        self.assertEqual(response.status_code, 200)
        refined = response.json()
        self.assertEqual(len(refined['proposals']), 5)
        self.assertNotIn('synthetic-proposal-value', str(refined))
        self.assertNotIn('synthetic-question-value', str(self.payloads))
        # Stored proposal text and IDs survive a second review/provider boundary.
        response = await self.client.post('/api/clarifications/decisions', json=self.body(refined,
            batch_id=refined['batch_id'], decisions=[dict(proposal_id=refined['proposals'][0]['id'], action='accept')]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['revision'], 4)
        self.assertNotIn('synthetic-proposal-value', str(await self.recovered()))

    async def test_invalid_structural_ids_rejected_at_request_and_provider_boundaries(self):
        state = await self.analyze()
        before = len(self.payloads)
        for extra in (dict(answers=[dict(question_id='invalid id', status='answered', answer='Literal')]),
                      dict(answers=[dict(question_id='unknown', status='answered', answer='Literal')])):
            response = await self.client.post('/api/clarifications/refine', json=self.body(state, **extra))
            self.assertEqual(response.status_code, 422)
        self.assertEqual(len(self.payloads), before)
        with patch.object(self.o._llm_client, 'refine_sections', return_value=dict(proposals=[dict(
            section_id='summary', replacement='Literal', reason='Explicit', question_ids=['invalid id'], conflict=False)])):
            response = await self.client.post('/api/clarifications/refine', json=self.body(state, answers=self.answers(state)))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(await self.recovered(), state)
        self.question_output[0]['id'] = 'invalid id'
        response = await self.client.post('/api/clarifications/analyze', json=self.body(state))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(await self.recovered(), state)
