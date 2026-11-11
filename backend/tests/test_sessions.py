"""Deterministic single-process session lifecycle tests; synthetic input only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import unittest
from unittest.mock import Mock, patch

import httpx2

from app import main
from app.jira_adapter import JiraAdapter
from app.llm_client import LLMClient
from app.masking import Masker
from app.orchestrator import IncompleteDraftError, Orchestrator, PreviewReviewRequired
from app.session_store import InvalidSessionIdError, SessionStore, SessionUnavailableError
from app.template_config import get_template
from app.validation import Validator
from app.schemas import TemplateReference


class FakeClock:
    def __init__(self):
        self.now = 0
        self.lock = Lock()

    def __call__(self):
        with self.lock:
            return self.now

    def set(self, now):
        with self.lock:
            self.now = now


class ObservedLock:
    """Expose that a second operation reached the wait, without sleeps."""
    def __init__(self):
        self.lock = Lock()
        self.waiting = Event()

    def acquire(self, blocking=True):
        if blocking and self.lock.locked():
            self.waiting.set()
        return self.lock.acquire(blocking=blocking)

    def release(self):
        self.lock.release()


def reference():
    return TemplateReference(mode="requirements", version=1)


def draft():
    return {field.key: "Synthetic requirement" for field in get_template("requirements").fields}


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = SessionStore(clock=self.clock)
        self.sid = self.store.create(Masker().context())

    def test_creation_initial_deadline_and_generator_format(self):
        self.assertEqual(len(self.sid), 22)
        self.store.validate_id(self.sid)
        self.assertEqual(self.store._sessions[self.sid].expires_at, 900)

    def test_success_refreshes_from_completion(self):
        self.clock.set(840)
        with self.store.operation(self.sid) as context:
            context.issued["synthetic-placeholder"] = "emails"
            self.clock.set(960)
        self.assertEqual(self.store._sessions[self.sid].expires_at, 1860)
        self.assertIn("synthetic-placeholder", self.store.get(self.sid).issued)

    def test_failure_preserves_deadline_and_rolls_back_metadata(self):
        self.clock.set(100)
        with self.assertRaisesRegex(ValueError, "synthetic failure"):
            with self.store.operation(self.sid) as context:
                context.issued["partial-change"] = "emails"
                raise ValueError("synthetic failure")
        self.assertEqual(self.store._sessions[self.sid].expires_at, 900)
        self.assertNotIn("partial-change", self.store.get(self.sid).issued)

    def test_failed_operation_crossing_expiry_cannot_revive(self):
        self.clock.set(840)
        with self.assertRaises(ValueError):
            with self.store.operation(self.sid) as context:
                context.issued["partial-change"] = "emails"
                self.clock.set(960)
                raise ValueError("synthetic failure")
        self.assertNotIn(self.sid, self.store._sessions)
        with self.assertRaises(SessionUnavailableError):
            with self.store.operation(self.sid):
                self.fail("Expired session was admitted")

    def test_immediately_before_boundary(self):
        self.clock.set(899.999)
        self.assertIsNotNone(self.store.get(self.sid))
        self.assertEqual(self.store._sessions[self.sid].expires_at, 900)

    def test_exact_boundary_is_expired(self):
        self.clock.set(900)
        self.assertIsNone(self.store.get(self.sid))
        self.assertNotIn(self.sid, self.store._sessions)

    def test_access_after_boundary(self):
        self.clock.set(901)
        with self.assertRaises(SessionUnavailableError):
            with self.store.operation(self.sid):
                self.fail("Expired session was admitted")

    def test_unknown_id(self):
        self.assertIsNone(self.store.get("A" * 22))
        with self.assertRaises(SessionUnavailableError):
            self.store.delete("A" * 22)

    def test_malformed_ids(self):
        for sid in [None, 123, "", "short", "A" * 23, "A" * 21 + "!", "A" * 21 + "\n"]:
            with self.subTest(sid=sid):
                with self.assertRaises(InvalidSessionIdError):
                    self.store.get(sid)
                with self.assertRaises(InvalidSessionIdError):
                    self.store.delete(sid)

    def test_delete_and_consume_are_terminal(self):
        for consume in [False, True]:
            sid = self.store.create(Masker().context())
            if consume:
                with self.store.operation(sid, consume=True):
                    pass
            else:
                self.store.delete(sid)
            with self.assertRaises(SessionUnavailableError):
                with self.store.operation(sid):
                    self.fail("Removed session revived")

    def test_inspection_returns_copy_and_does_not_refresh(self):
        context = self.store.get(self.sid)
        context.issued["external-mutation"] = "emails"
        self.assertNotIn("external-mutation", self.store.get(self.sid).issued)
        self.assertEqual(self.store._sessions[self.sid].expires_at, 900)

    def test_cleanup_removes_untouched_expired_only(self):
        self.clock.set(100)
        other = self.store.create(Masker().context())
        self.clock.set(900)
        self.assertEqual(self.store.cleanup_expired(), 1)
        self.assertNotIn(self.sid, self.store._sessions)
        self.assertIn(other, self.store._sessions)
        self.assertEqual(self.store._sessions[other].expires_at, 1000)

    def test_cleanup_does_not_wait_on_active_lock(self):
        entry = self.store._sessions[self.sid]
        entry.lock.acquire()
        try:
            self.clock.set(900)
            self.assertEqual(self.store.cleanup_expired(), 0)
        finally:
            entry.lock.release()
        self.assertEqual(self.store.cleanup_expired(), 1)


class OrchestratorFixture:
    def setUp(self):
        self.clock = FakeClock()
        self.store = SessionStore(clock=self.clock)
        self.client = Mock(wraps=LLMClient())
        self.jira = Mock(wraps=JiraAdapter())
        self.orchestrator = Orchestrator(Masker(), self.store, self.client, Validator(), self.jira)
        self.sid, self.preview = self.orchestrator.mask_preview("Synthetic context")


class OrchestratorTests(OrchestratorFixture, unittest.TestCase):
    def test_valid_generation_refresh(self):
        self.clock.set(100)
        result = self.orchestrator.generate_draft(self.sid, self.preview).draft
        self.assertIn("summary", result)
        self.assertEqual(self.store._sessions[self.sid].expires_at, 1000)

    def test_preview_review_commits_metadata_refreshes_without_generation(self):
        self.clock.set(100)
        before = self.store.get(self.sid).issued.copy()
        with self.assertRaises(PreviewReviewRequired) as result:
            self.orchestrator.generate_draft(self.sid, self.preview + " password=demo-preview")
        self.assertNotIn("demo-preview", result.exception.masked_text)
        self.assertNotEqual(self.store.get(self.sid).issued, before)
        self.assertEqual(self.store._sessions[self.sid].expires_at, 1000)
        self.client.generate_draft.assert_not_called()

    def test_incomplete_submission_no_refresh_or_metadata_commit(self):
        self.clock.set(100)
        before = self.store.get(self.sid).issued.copy()
        with self.assertRaises(IncompleteDraftError):
            self.orchestrator.create_ticket(self.sid, {"summary": "password=demo-partial"}, reference())
        self.assertEqual(self.store._sessions[self.sid].expires_at, 900)
        self.assertEqual(self.store.get(self.sid).issued, before)
        self.jira.create_ticket.assert_not_called()

    def test_invalid_generated_output_no_refresh(self):
        self.clock.set(100)
        self.client.generate_draft.return_value = {"unknown": "synthetic"}
        with self.assertRaises(ValueError):
            self.orchestrator.generate_draft(self.sid, self.preview)
        self.assertEqual(self.store._sessions[self.sid].expires_at, 900)

    def test_output_processing_failure_rolls_back_context(self):
        self.client.generate_draft.return_value = {"summary": "person@example.test"}
        self.orchestrator._validator = Mock(wraps=Validator())
        self.orchestrator._validator.find_missing.side_effect = ValueError("synthetic failure")
        with self.assertRaises(ValueError):
            self.orchestrator.generate_draft(self.sid, self.preview)
        self.assertEqual(self.store.get(self.sid).issued, {})
        self.assertEqual(self.store._sessions[self.sid].expires_at, 900)

    def test_expired_ticket_does_not_reach_jira(self):
        self.clock.set(900)
        with self.assertRaises(SessionUnavailableError):
            self.orchestrator.create_ticket(self.sid, draft(), reference())
        self.jira.create_ticket.assert_not_called()

    def test_successful_submission_consumes(self):
        ticket = self.orchestrator.create_ticket(self.sid, draft(), reference())
        self.assertTrue(ticket.key.startswith("MOCK-"))
        self.assertIsNone(self.store.get(self.sid))
        with self.assertRaises(SessionUnavailableError):
            self.orchestrator.create_ticket(self.sid, draft(), reference())
        self.assertEqual(self.jira.create_ticket.call_count, 1)

    def test_submission_failure_does_not_consume_or_refresh(self):
        self.jira.create_ticket.side_effect = ValueError("synthetic failure")
        self.clock.set(100)
        with self.assertRaises(ValueError):
            self.orchestrator.create_ticket(self.sid, draft(), reference())
        self.assertIsNotNone(self.store.get(self.sid))
        self.assertEqual(self.store._sessions[self.sid].expires_at, 900)


class ConcurrencyTests(OrchestratorFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.lock = ObservedLock()
        self.store._sessions[self.sid].lock = self.lock
        self.started = Event()
        self.release = Event()

    def blocked_generation(self, text, template):
        self.started.set()
        if not self.release.wait(3):
            raise AssertionError("Test did not release generation")
        return LLMClient().generate_draft(text, template)

    def test_generation_crosses_expiry_and_cleanup_preserves_it(self):
        self.client.generate_draft.side_effect = self.blocked_generation
        self.clock.set(840)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.orchestrator.generate_draft, self.sid, self.preview)
            try:
                self.assertTrue(self.started.wait(3))
                self.clock.set(960)
                self.assertIsNone(self.store.get(self.sid))
                self.assertEqual(self.store.cleanup_expired(), 0)
                # Independent sessions can proceed while generation holds its lock.
                other = self.store.create(Masker().context())
                with self.store.operation(other):
                    pass
                with self.assertRaises(SessionUnavailableError):
                    self.orchestrator.generate_draft(self.sid, self.preview)
            finally:
                self.release.set()
            first.result(timeout=3)
        self.assertEqual(self.store._sessions[self.sid].expires_at, 1860)
        self.assertIsNotNone(self.store.get(self.sid))

    def test_predeadline_waiter_remains_eligible_after_wait(self):
        self.client.generate_draft.side_effect = self.blocked_generation
        self.clock.set(840)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.orchestrator.generate_draft, self.sid, self.preview)
            try:
                self.assertTrue(self.started.wait(3))
                second = pool.submit(self.orchestrator.generate_draft, self.sid, self.preview)
                self.assertTrue(self.lock.waiting.wait(3))
                self.clock.set(960)
                self.assertFalse(second.done())
            finally:
                self.release.set()
            first.result(timeout=3)
            second.result(timeout=3)
        self.assertEqual(self.client.generate_draft.call_count, 2)
        self.assertEqual(self.store._sessions[self.sid].expires_at, 1860)

    def test_late_arrival_cannot_wait_for_an_earlier_refresh(self):
        self.client.generate_draft.side_effect = self.blocked_generation
        self.clock.set(840)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.orchestrator.generate_draft, self.sid, self.preview)
            try:
                self.assertTrue(self.started.wait(3))
                self.clock.set(900)
                late = pool.submit(self.orchestrator.generate_draft, self.sid, self.preview)
                with self.assertRaises(SessionUnavailableError):
                    late.result(timeout=3)
                self.assertFalse(self.lock.waiting.is_set())
            finally:
                self.release.set()
            first.result(timeout=3)
        self.assertEqual(self.client.generate_draft.call_count, 1)
        self.assertEqual(self.store._sessions[self.sid].expires_at, 1800)

    def test_queued_generation_cannot_revive_deleted_session(self):
        def deletion():
            with self.store.operation(self.sid, consume=True):
                self.started.set()
                if not self.release.wait(3):
                    raise AssertionError("Test did not release deletion")
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(deletion)
            try:
                self.assertTrue(self.started.wait(3))
                second = pool.submit(self.orchestrator.generate_draft, self.sid, self.preview)
                self.assertTrue(self.lock.waiting.wait(3))
            finally:
                self.release.set()
            first.result(timeout=3)
            with self.assertRaises(SessionUnavailableError):
                second.result(timeout=3)
        self.client.generate_draft.assert_not_called()
        self.assertNotIn(self.sid, self.store._sessions)

    def test_failed_generation_crossing_expiry_does_not_revive(self):
        def fail(text, template):
            self.blocked_generation(text, template)
            raise ValueError("synthetic failure")
        self.client.generate_draft.side_effect = fail
        self.clock.set(840)
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.orchestrator.generate_draft, self.sid, self.preview)
            try:
                self.assertTrue(self.started.wait(3))
                self.clock.set(960)
                self.assertEqual(self.store.cleanup_expired(), 0)
            finally:
                self.release.set()
            with self.assertRaises(ValueError):
                first.result(timeout=3)
        self.assertNotIn(self.sid, self.store._sessions)

    def test_delete_waits_for_generation_then_removes_entry(self):
        self.client.generate_draft.side_effect = self.blocked_generation
        self.clock.set(840)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.orchestrator.generate_draft, self.sid, self.preview)
            try:
                self.assertTrue(self.started.wait(3))
                deletion = pool.submit(self.orchestrator.delete_session, self.sid)
                self.assertTrue(self.lock.waiting.wait(3))
                self.clock.set(960)
                self.assertFalse(deletion.done())
            finally:
                self.release.set()
            first.result(timeout=3)
            deletion.result(timeout=3)
        self.assertIsNone(self.store.get(self.sid))

    def test_concurrent_submissions_create_at_most_one_ticket(self):
        adapter = JiraAdapter()
        def submit(fields, template):
            self.started.set()
            if not self.release.wait(3):
                raise AssertionError("Test did not release submission")
            return adapter.create_ticket(fields, template)
        self.jira.create_ticket.side_effect = submit
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.orchestrator.create_ticket, self.sid, draft(), reference())
            try:
                self.assertTrue(self.started.wait(3))
                second = pool.submit(self.orchestrator.create_ticket, self.sid, draft(), reference())
                self.assertTrue(self.lock.waiting.wait(3))
                self.clock.set(960)
                self.assertEqual(self.store.cleanup_expired(), 0)
            finally:
                self.release.set()
            first.result(timeout=3)
            with self.assertRaises(SessionUnavailableError):
                second.result(timeout=3)
        self.assertEqual(len(adapter._tickets), 1)
        self.assertEqual(self.jira.create_ticket.call_count, 1)
        self.assertIsNone(self.store.get(self.sid))


class SessionAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = FakeClock()
        self.store = SessionStore(clock=self.clock)
        self.jira = Mock(wraps=JiraAdapter())
        self.orchestrator = Orchestrator(Masker(), self.store, LLMClient(), Validator(), self.jira)
        self.override = patch("app.main.orchestrator", self.orchestrator)
        self.override.start()
        self.client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), base_url="http://synthetic.test")
        self.sid, self.preview = self.orchestrator.mask_preview("Synthetic context")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.override.stop()

    async def requests_for(self, sid):
        return [
            await self.client.post("/api/generate-draft", json={"session_id": sid, "masked_text": self.preview}),
            await self.client.post("/api/create-ticket", json={"session_id": sid, "template": reference().model_dump(), "draft": draft()}),
            await self.client.delete(f"/api/sessions/{sid}"),
        ]

    async def test_unknown_expired_deleted_consumed_same_safe_response(self):
        deleted = self.store.create(Masker().context())
        self.store.delete(deleted)
        consumed = self.store.create(Masker().context())
        self.orchestrator.create_ticket(consumed, draft(), reference())
        self.jira.reset_mock()
        self.clock.set(900)
        for sid in ["A" * 22, self.sid, deleted, consumed]:
            for response in await self.requests_for(sid):
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"detail": {
                    "code": "SESSION_UNAVAILABLE",
                    "message": "Session is unavailable. Restart the workflow.",
                }})
                self.assertNotIn(sid, response.text)
        self.jira.create_ticket.assert_not_called()

    async def test_missing_null_malformed_and_wrong_type_ids(self):
        for sid in [None, 123, "", "short", "A" * 21 + "!", "A" * 21 + "\n"]:
            for route, extra in [("/api/generate-draft", {"masked_text": self.preview}),
                                 ("/api/create-ticket", {"template": reference().model_dump(), "draft": draft()})]:
                for body in [extra, {**extra, "session_id": sid}]:
                    response = await self.client.post(route, json=body)
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.json()["detail"]["code"], "INVALID_REQUEST")
        for sid in ["short", "A" * 21 + "!"]:
            response = await self.client.delete(f"/api/sessions/{sid}")
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["code"], "INVALID_REQUEST")
        self.jira.create_ticket.assert_not_called()

    async def test_successful_deletion_and_consumption(self):
        response = await self.client.delete(f"/api/sessions/{self.sid}")
        self.assertEqual(response.json(), {"status": "deleted"})
        self.assertIsNone(self.store.get(self.sid))
        sid = self.store.create(Masker().context())
        response = await self.client.post("/api/create-ticket", json={"session_id": sid, "template": reference().model_dump(), "draft": draft()})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.store.get(sid))

    async def test_session_independent_reads_do_not_refresh(self):
        self.clock.set(100)
        for route in ["/api/health", "/api/templates/requirements", "/api/mask-rules"]:
            response = await self.client.get(route)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store._sessions[self.sid].expires_at, 900)

    async def test_generation_review_refresh_and_failed_submission(self):
        self.clock.set(100)
        response = await self.client.post("/api/generate-draft", json={"session_id": self.sid, "masked_text": self.preview + " token=demo-new"})
        self.assertEqual(response.json()["status"], "preview_review_required")
        self.assertEqual(self.store._sessions[self.sid].expires_at, 1000)
        self.clock.set(200)
        response = await self.client.post("/api/generate-draft", json={"session_id": self.sid, "masked_text": response.json()["masked_text"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store._sessions[self.sid].expires_at, 1100)
        self.clock.set(300)
        response = await self.client.post("/api/create-ticket", json={"session_id": self.sid, "template": reference().model_dump(), "draft": {}})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["message"], "Draft is incomplete")
        self.assertEqual(self.store._sessions[self.sid].expires_at, 1100)


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_initial_cleanup_and_shutdown_awaits_task(self):
        clock = FakeClock()
        store = SessionStore(clock=clock)
        sid = store.create(Masker().context())
        clock.set(900)
        with patch("app.main.session_store", store):
            async with main.app.router.lifespan_context(main.app):
                self.assertNotIn(sid, store._sessions)
                task = main.app.state.session_cleanup_task
                self.assertFalse(task.done())
                self.assertEqual(task.get_name(), "session-cleanup")
            self.assertTrue(task.done())
            self.assertTrue(task.cancelled())
            self.assertNotIn(task, asyncio.all_tasks())

    async def test_shutdown_cancels_an_executing_cleanup_loop(self):
        started = asyncio.Event()
        stopped = asyncio.Event()
        async def controlled_loop(store):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        with patch("app.main.cleanup_loop", controlled_loop):
            async with main.app.router.lifespan_context(main.app):
                await asyncio.wait_for(started.wait(), 3)
                task = main.app.state.session_cleanup_task
            self.assertTrue(stopped.is_set())
            self.assertTrue(task.cancelled())
            self.assertNotIn(task, asyncio.all_tasks())

    async def test_periodic_cleanup_failure_recovers_and_cancels(self):
        store = Mock()
        store.cleanup_expired.side_effect = [RuntimeError("password=demo-error"), 1]
        ticks = asyncio.Queue()
        waiting = asyncio.Queue()
        async def wait(interval):
            self.assertEqual(interval, 30)
            await waiting.put(True)
            await ticks.get()
        task = asyncio.create_task(main.cleanup_loop(store, wait))
        try:
            await asyncio.wait_for(waiting.get(), 3)
            with self.assertLogs("app.main", level="WARNING") as logs:
                await ticks.put(True)
                await asyncio.wait_for(waiting.get(), 3)
            self.assertEqual(logs.output, ["WARNING:app.main:Session cleanup failed; will retry"])
            await ticks.put(True)
            await asyncio.wait_for(waiting.get(), 3)
            self.assertEqual(store.cleanup_expired.call_count, 2)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(task.done())

    async def test_startup_failure_does_not_prevent_lifespan(self):
        store = Mock()
        store.cleanup_expired.side_effect = RuntimeError("password=demo-error")
        with patch("app.main.session_store", store), self.assertLogs("app.main", level="WARNING") as logs:
            async with main.app.router.lifespan_context(main.app):
                task = main.app.state.session_cleanup_task
        self.assertTrue(task.cancelled())
        self.assertNotIn("demo-error", repr(logs.output))


if __name__ == "__main__":
    unittest.main()
