"""Synthetic protection regressions. SDK calls always mocked."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import logging
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx2
from app import abuse_protection as ap, config, main, openai_client
from app.jira_adapter import JiraAdapter
from app.llm_client import LLMClient, RequirementsOutput, OpenAIRequirementsGenerator
from app.masking import Masker, SanitizedText
from app.openai_client import LLMError, APITimeoutError
from app.orchestrator import Orchestrator
from app.refinement_schemas import QuestionBatch, ProposalBatch
from app.safe_logging import configure_safe_logging
from app.session_store import SessionStore
from app.validation import Validator


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.guard = ap.Protection(lambda: self.now)

    def test_rate_boundaries_shared_route_bucket_and_expiry(self):
        for kind, limit in [('session', 5), ('llm', 6)]:
            for _ in range(limit):
                self.guard.rate('synthetic', kind)
            with self.assertRaises(ap.ProtectionError) as caught:
                self.guard.rate('synthetic', kind)
            self.assertEqual((caught.exception.status, caught.exception.retry_after), (429, 60))
        self.now = 59.9
        with self.assertRaises(ap.ProtectionError):
            self.guard.rate('synthetic', 'llm')
        self.now = 60
        self.guard.rate('synthetic', 'llm')

    def test_concurrent_rate_boundary(self):
        def attempt(_):
            try:
                self.guard.rate('synthetic', 'session')
                return True
            except ap.ProtectionError:
                return False
        with ThreadPoolExecutor(20) as pool:
            self.assertEqual(sum(pool.map(attempt, range(100))), 5)

    def test_identity_storage_does_not_evict_active_limits(self):
        for i in range(2048):
            self.guard.rate(str(i), 'session')
        with self.assertRaises(ap.ProtectionError):
            self.guard.rate('new', 'session')
        self.assertEqual(len(self.guard.identities), 2048)
        self.now = 60
        self.guard.rate('new', 'session')
        self.assertEqual(len(self.guard.identities), 1)

    def test_concurrency_and_permit_release_on_base_exception(self):
        with self.guard.dispatch(ap.PaidAccount(), 'generation'):
            try:
                with self.guard.dispatch(ap.PaidAccount(), 'generation'):
                    with self.assertRaises(ap.ProtectionError):
                        with self.guard.dispatch(ap.PaidAccount(), 'generation'):
                            self.fail()
                    raise asyncio.CancelledError()
            except asyncio.CancelledError:
                pass
            self.assertEqual(self.guard.active, 1)
        self.assertEqual(self.guard.active, 0)
        self.assertEqual(self.guard.reserved, 2 * 130932)

    def test_atomic_budget_no_refunds_and_restart_reset(self):
        self.guard.reserved = ap.BUDGET_MICRODOLLARS - 130932
        def attempt(_):
            try:
                with self.guard.dispatch(ap.PaidAccount(), 'generation'):
                    pass
                return True
            except ap.ProtectionError:
                return False
        with ThreadPoolExecutor(20) as pool:
            self.assertEqual(sum(pool.map(attempt, range(40))), 1)
        self.assertEqual(self.guard.reserved, ap.BUDGET_MICRODOLLARS)
        self.assertEqual(ap.Protection().reserved, 0)

    def test_global_dispatch_window(self):
        for _ in range(30):
            with self.guard.dispatch(ap.PaidAccount(), 'analysis'):
                pass
        with self.assertRaises(ap.ProtectionError) as caught:
            with self.guard.dispatch(ap.PaidAccount(), 'analysis'):
                pass
        self.assertEqual(caught.exception.status, 429)
        self.now = 60
        with self.guard.dispatch(ap.PaidAccount(), 'analysis'):
            pass

    def test_outstanding_operations_boundary(self):
        with ExitStack() as stack:
            for _ in range(16):
                stack.enter_context(self.guard.operation())
            with self.assertRaises(ap.ProtectionError):
                with self.guard.operation():
                    pass
        self.assertEqual(self.guard.outstanding, 0)

    def test_proxy_identity(self):
        networks = ap.trusted_networks('10.0.0.0/24,::1/128')
        def scope(peer, header):
            return dict(client=(peer, 123), headers=[(b'x-forwarded-for', header)])
        self.assertEqual(ap.client_identity(scope('192.0.2.1', b'198.51.100.1'), networks), '192.0.2.1')
        self.assertEqual(ap.client_identity(scope('10.0.0.1', b'203.0.113.9, 192.0.2.1'), networks), '192.0.2.1')
        self.assertEqual(ap.client_identity(scope('10.0.0.1', b'192.0.2.1, 10.0.0.2'), networks), '192.0.2.1')
        for bad in (b'garbage', b'', b'192.0.2.1, nope', b'\xff', b'1.2.3.4:123'):
            self.assertEqual(ap.client_identity(scope('10.0.0.1', bad), networks), '10.0.0.1')
        duplicate = scope('10.0.0.1', b'192.0.2.1')
        duplicate['headers'] *= 2
        self.assertEqual(ap.client_identity(duplicate, networks), '10.0.0.1')
        self.assertEqual(ap.client_identity(scope('::1', b'2001:db8::1'), networks), '2001:db8::1')
        with self.assertRaises(ValueError):
            ap.trusted_networks('*', production=True)

    def test_placeholder_atomic_bound_and_retained_entries(self):
        masker = Masker()
        context = masker.context(set())
        context.issued = {f'synthetic-{i}': 'secrets' for i in range(1999)}
        before = context.issued.copy()
        with self.assertRaises(ap.ProtectionError):
            masker.sanitize_fields({'a': 'password=synthetic-one', 'b': 'password=synthetic-two'}, context)
        self.assertEqual(context.issued, before)
        safe = masker.sanitize_fields({'a': 'password=synthetic-one'}, context)
        self.assertEqual(len(context.issued), 2000)
        self.assertNotIn('synthetic-one', repr(context.issued))
        self.assertEqual(masker.sanitize_fields(safe, context), safe)
        self.assertEqual(masker.restore(safe['a'], context, set()), safe['a'])


class ConnectorGuardTests(unittest.TestCase):
    def setUp(self):
        self.guard = ap.Protection()
        self.account = ap.PaidAccount()
        token = ap.paid_session.set(self.account)
        self.addCleanup(ap.paid_session.reset, token)
        for obj, name, value in [(openai_client, 'protection', self.guard), (config, 'OPENAI_MODEL', ap.MODEL)]:
            p = patch.object(obj, name, value); p.start(); self.addCleanup(p.stop)
        self.client = openai_client.OpenAIClient()
        self.client._client = Mock()

    def test_caps_complete_and_incomplete_structured_results(self):
        for schema, value, cap in [(RequirementsOutput, dict.fromkeys(('summary', 'business_context', 'requirements', 'acceptance_criteria', 'open_questions'), ''), 4096),
                                   (QuestionBatch, {'questions': []}, 2048), (ProposalBatch, {'proposals': []}, 4096)]:
            user = '{}' if schema is not RequirementsOutput else 'Synthetic feature'
            self.client._client.responses.parse.return_value = SimpleNamespace(status='completed', output=[], output_parsed=schema(**value))
            self.client.ask(SanitizedText('Static rules'), SanitizedText(user), schema)
            args = self.client._client.responses.parse.call_args.kwargs
            self.assertEqual(args['max_output_tokens'], cap)
            self.assertFalse(args['store'])
            self.assertEqual(args['service_tier'], 'default')
            self.client._client.responses.parse.return_value.status = 'incomplete'
            with self.assertRaises(LLMError):
                self.client.ask(SanitizedText('Static rules'), SanitizedText(user), schema)
        self.assertEqual(self.account.calls, 6)
        self.assertEqual(self.guard.active, 0)

    def test_failure_quota_survives_session_rollback(self):
        store = SessionStore()
        o = Orchestrator(Masker(), store, LLMClient(), Validator(), JiraAdapter())
        sid, text = o.mask_preview('Synthetic feature', [])
        generator = OpenAIRequirementsGenerator()
        generator._client = self.client
        o._llm_client = generator
        self.client._client.responses.parse.side_effect = RuntimeError('token=synthetic-canary')
        before = store.get(sid)
        for _ in range(12):
            with self.assertRaises(LLMError):
                o.generate_draft(sid, text)
        with self.assertRaises(ap.ProtectionError) as caught:
            o.generate_draft(sid, text)
        self.assertEqual(caught.exception.code, 'SESSION_LLM_LIMIT_REACHED')
        self.assertEqual(store.get(sid), before)
        self.assertEqual(self.client._client.responses.parse.call_count, 12)
        self.assertEqual(self.guard.reserved, 12 * 130932)
        self.assertEqual(self.guard.active, 0)

    def test_timeout_release_and_oversized_input_without_charge(self):
        self.client._client.responses.parse.side_effect = APITimeoutError(request=Mock())
        with self.assertRaises(LLMError):
            self.client.ask(SanitizedText('rules'), SanitizedText('Synthetic'), RequirementsOutput)
        self.assertEqual(self.guard.active, 0)
        before = self.guard.reserved
        with self.assertRaises(ap.ProtectionError):
            self.client.ask(SanitizedText('rules'), SanitizedText('a' * 131072), RequirementsOutput)
        self.assertEqual(self.guard.reserved, before)
        self.assertEqual(self.client._client.responses.parse.call_count, 1)

    def test_unknown_model_and_missing_account_fail_closed(self):
        with patch.object(config, 'OPENAI_MODEL', 'unreviewed'):
            with self.assertRaises(ap.ProtectionError):
                self.client.ask(SanitizedText('rules'), SanitizedText('Synthetic'), RequirementsOutput)
        token = ap.paid_session.set(None)
        try:
            with self.assertRaises(ap.ProtectionError):
                self.client.ask(SanitizedText('rules'), SanitizedText('Synthetic'), RequirementsOutput)
        finally:
            ap.paid_session.reset(token)
        self.client._client.responses.parse.assert_not_called()


class APIGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.guard = ap.Protection()
        self.store = SessionStore()
        self.o = Orchestrator(Masker(), self.store, LLMClient(), Validator(), JiraAdapter())
        for obj, name, value in [(ap, 'protection', self.guard), (main, 'protection', self.guard),
                                 (openai_client, 'protection', self.guard), (main, 'orchestrator', self.o),
                                 (config, 'OPENAI_MODEL', ap.MODEL)]:
            p = patch.object(obj, name, value); p.start(); self.addCleanup(p.stop)
        self.client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), base_url='http://synthetic.test')
        self.addAsyncCleanup(self.client.aclose)

    async def test_api_rate_retry_cors_and_non_llm_routes(self):
        for _ in range(5):
            r = await self.client.post('/api/mask-preview', json={'text': 'Synthetic'})
            self.assertEqual(r.status_code, 200)
        r = await self.client.post('/api/mask-preview', json={'text': 'Synthetic'}, headers={'Origin': 'http://localhost:5173', 'X-Forwarded-For': '203.0.113.1'})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.headers['retry-after'], '60')
        self.assertIn('Retry-After', r.headers['access-control-expose-headers'])
        self.assertEqual((await self.client.get('/api/health')).status_code, 200)
        for path in ['/api/generate-draft', '/api/clarifications/analyze', '/api/clarifications/refine'] * 2:
            self.assertEqual((await self.client.post(path, json={})).status_code, 422)
        self.assertEqual((await self.client.post('/api/generate-draft', json={})).status_code, 429)

    async def test_cancelled_http_worker_keeps_permit_and_outstanding_slot(self):
        started, finish, ended = Event(), Event(), Event()
        def parse(**kwargs):
            started.set()
            try:
                if not finish.wait(5):
                    raise RuntimeError('synthetic timeout')
                raise RuntimeError('synthetic failure')
            finally:
                ended.set()
        provider = OpenAIRequirementsGenerator()
        provider._client._client = Mock()
        provider._client._client.responses.parse.side_effect = parse
        self.o._llm_client = provider
        sid, text = self.o.mask_preview('Synthetic', [])
        task = asyncio.create_task(main.run_session_operation(self.o.generate_draft, sid, text))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 3))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(self.guard.active, 1)
            self.assertEqual(self.guard.outstanding, 1)
            self.assertEqual(self.guard.reserved, 130932)
        finally:
            finish.set()
            await asyncio.to_thread(ended.wait, 3)
            for _ in range(100):
                if not self.guard.outstanding:
                    break
                await asyncio.sleep(.01)
        self.assertEqual((self.guard.active, self.guard.outstanding), (0, 0))

    async def test_safe_logging(self):
        configure_safe_logging()
        for name in ('uvicorn.access', 'openai', 'httpx2', 'httpcore2'):
            self.assertTrue(logging.getLogger(name).disabled)
        with self.assertLogs('app.main', level='WARNING') as logs:
            r = await main.llm_error(None, LLMError('token=synthetic-canary'))
        self.assertNotIn('synthetic-canary', repr(logs.output))
        self.assertNotIn('synthetic-canary', r.body.decode())

class AdditionalRegressionTests(unittest.TestCase):
    setUp = ConnectorGuardTests.setUp

    def test_simultaneous_mock_sdk_calls_cannot_bypass_concurrency(self):
        from threading import Barrier
        barrier, release = Barrier(3), Event()
        def parse(**kwargs):
            barrier.wait(timeout=3)
            release.wait(3)
            raise RuntimeError('synthetic failure')
        self.client._client.responses.parse.side_effect = parse
        def call():
            token = ap.paid_session.set(ap.PaidAccount())
            try:
                self.client.ask(SanitizedText('Rules'), SanitizedText('Synthetic'), RequirementsOutput)
            except (ap.ProtectionError, LLMError) as error:
                return type(error)
            finally:
                ap.paid_session.reset(token)
        with ThreadPoolExecutor(3) as pool:
            first, second = pool.submit(call), pool.submit(call)
            try:
                barrier.wait(timeout=3)
                self.assertEqual(pool.submit(call).result(timeout=3), ap.ProtectionError)
                self.assertEqual(self.guard.active, 2)
                self.assertEqual(self.client._client.responses.parse.call_count, 2)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=3), LLMError)
            self.assertEqual(second.result(timeout=3), LLMError)
        self.assertEqual(self.guard.active, 0)

    def test_safe_server_logs_strip_paths_identifiers_and_exception(self):
        configure_safe_logging()
        with self.assertLogs('uvicorn.error', level='WARNING') as captured:
            try:
                raise RuntimeError('token=synthetic-secret')
            except RuntimeError:
                logging.getLogger('uvicorn.error').exception('/api/sessions/synthetic-session-id')
        self.assertNotIn('synthetic', repr(captured.output))

    def test_unknown_pricing_fails_at_startup(self):
        with patch.object(config, 'LLM_PROVIDER', 'openai'), patch.object(config, 'OPENAI_MODEL', 'unreviewed'):
            with self.assertRaises(ValueError):
                config.validate_llm_config()

class FinalBoundaryTests(unittest.TestCase):
    setUp = ConnectorGuardTests.setUp

    def test_exact_outbound_bytes_and_multibyte_rejection(self):
        self.client._client.responses.parse.return_value = SimpleNamespace(
            status='completed', output=[], output_parsed=RequirementsOutput(**dict.fromkeys(
                ('summary', 'business_context', 'requirements', 'acceptance_criteria', 'open_questions'), '')))
        import json
        self.client.ask(SanitizedText('Rules'), SanitizedText(''), RequirementsOutput)
        args = self.client._client.responses.parse.call_args.kwargs
        envelope = {k: v for k, v in args.items() if k != 'text_format'}
        envelope['text'] = {'format': openai_client.type_to_text_format_param(RequirementsOutput)}
        available = ap.MAX_OUTBOUND_BYTES - len(json.dumps(envelope, ensure_ascii=False).encode())
        self.client.ask(SanitizedText('Rules'), SanitizedText('a' * available), RequirementsOutput)
        count = self.client._client.responses.parse.call_count
        for text in ('a' * (available + 1), 'я' * (available // 2 + 1)):
            with self.assertRaises(ap.ProtectionError):
                self.client.ask(SanitizedText('Rules'), SanitizedText(text), RequirementsOutput)
        self.assertEqual(self.client._client.responses.parse.call_count, count)

    def test_descendant_sdk_logging_has_no_transport_output(self):
        import io
        parent = logging.getLogger('openai')
        output = io.StringIO()
        parent.addHandler(logging.StreamHandler(output))
        configure_safe_logging()
        child = logging.getLogger('openai._base_client')
        with patch.object(child, 'level', logging.DEBUG):
            child.error('Authorization: token=synthetic-canary')
        self.assertEqual(output.getvalue(), '')

    def test_placeholder_overflow_preserves_session_revision_and_ttl(self):
        store = SessionStore(clock=lambda: 100)
        o = Orchestrator(Masker(), store, LLMClient(), Validator(), JiraAdapter())
        sid, text = o.mask_preview('Synthetic', [])
        o.generate_draft(sid, text)
        with store.operation(sid) as context:
            context.issued.update({f'synthetic-{i}': 'secrets' for i in range(2000)})
        before, ttl = store.get(sid), store._sessions[sid].expires_at
        with self.assertRaises(ap.ProtectionError):
            o.generate_draft(sid, 'password=synthetic-new')
        self.assertEqual(store.get(sid), before)
        self.assertEqual(store._sessions[sid].expires_at, ttl)

class ConcurrentHTTPTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = APIGuardTests.asyncSetUp

    async def test_concurrent_http_rate_boundary(self):
        results = await asyncio.gather(*(self.client.post('/api/mask-preview', json={'text': 'Synthetic'}) for _ in range(20)))
        self.assertEqual(sum(r.status_code == 200 for r in results), 5)
        self.assertEqual(sum(r.status_code == 429 for r in results), 15)
        self.assertEqual(len(self.store._sessions), 5)


    async def test_api_outstanding_capacity_error_is_safe(self):
        sid, text = self.o.mask_preview('Synthetic', [])
        with ExitStack() as stack:
            for _ in range(16):
                stack.enter_context(self.guard.operation())
            r = await self.client.get(f'/api/sessions/{sid}/refinement')
            self.assertEqual(r.status_code, 503)
            self.assertEqual(r.json()['detail']['code'], 'OPERATION_CAPACITY_EXHAUSTED')
            self.assertNotIn(sid, r.text)
            self.assertEqual(r.headers['retry-after'], '2')
        self.assertEqual(self.guard.outstanding, 0)

    async def test_pending_worker_cancellation_releases_slot_without_dispatch(self):
        from anyio import to_thread
        limiter = to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        limiter.total_tokens = 1
        await limiter.acquire()
        sid, text = self.o.mask_preview('Synthetic', [])
        task = asyncio.create_task(main.run_session_operation(self.o.generate_draft, sid, text))
        try:
            for _ in range(100):
                if self.guard.outstanding:
                    break
                await asyncio.sleep(.001)
            self.assertEqual(self.guard.outstanding, 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(self.guard.outstanding, 0)
            self.assertEqual(self.guard.reserved, 0)
            self.assertEqual(self.store._sessions[sid].admitted, 0)
        finally:
            limiter.release()
            limiter.total_tokens = previous
