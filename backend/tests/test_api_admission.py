"""API admission regressions using real endpoints and controlled worker capacity."""
import asyncio
from threading import Event
import unittest
from unittest.mock import Mock, patch

from anyio import to_thread
import httpx2

from app import main
from app.jira_adapter import JiraAdapter
from app.llm_client import LLMClient
from app.masking import Masker
from app.orchestrator import Orchestrator
from app.session_store import SessionStore, SessionUnavailableError
from app.validation import Validator
from test_sessions import FakeClock, draft


class APIAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = FakeClock()
        self.store = SessionStore(clock=self.clock)
        self.llm = Mock(wraps=LLMClient())
        self.jira = Mock(wraps=JiraAdapter())
        self.orchestrator = Orchestrator(Masker(), self.store, self.llm, Validator(), self.jira)
        self.override = patch("app.main.orchestrator", self.orchestrator)
        self.override.start()
        self.client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=main.app), base_url="http://synthetic.test"
        )
        self.loop = asyncio.get_running_loop()
        self.limiter = to_thread.current_default_thread_limiter()
        self.old_tokens = self.limiter.total_tokens
        self.limiter.total_tokens = 1
        self.admissions = asyncio.Queue()
        real_admit = self.store.admit

        def observe_admission(sid):
            reservation = real_admit(sid)  # Production eligibility logic, no replacement.
            self.admissions.put_nowait(reservation)
            return reservation

        self.store.admit = Mock(side_effect=observe_admission)

    async def asyncTearDown(self):
        await self.client.aclose()
        self.limiter.total_tokens = self.old_tokens
        self.override.stop()

    def new_session(self):
        return self.orchestrator.mask_preview("Synthetic context")

    async def request(self, kind, sid, preview):
        if kind == "generation":
            return await self.client.post("/api/generate-draft", json={
                "session_id": sid, "masked_text": preview,
            })
        if kind == "submission":
            return await self.client.post("/api/create-ticket", json={
                "session_id": sid, "draft": draft(),
            })
        return await self.client.delete(f"/api/sessions/{sid}")

    async def admitted(self):
        return await asyncio.wait_for(self.admissions.get(), 3)

    def assert_unavailable(self, response):
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": {
            "code": "SESSION_UNAVAILABLE",
            "message": "Session is unavailable. Restart the workflow.",
        }})

    def observe_completion(self, method):
        """Notify only after production operation.finally has released its pin."""
        complete = asyncio.Event()
        original = getattr(self.orchestrator, method)

        def observed(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            finally:
                self.loop.call_soon_threadsafe(complete.set)

        setattr(self.orchestrator, method, observed)
        return complete

    async def cancel_request(self, task):
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_r1_late_requests_rejected_for_all_endpoints_before_worker_queue(self):
        sid, preview = self.new_session()
        entry = self.store._sessions[sid]
        started = asyncio.Event()
        release = Event()

        def generation(text, template):
            self.loop.call_soon_threadsafe(started.set)
            if not release.wait(3):
                raise AssertionError("Generation was not released")
            return LLMClient().generate_draft(text, template)

        self.llm.generate_draft.side_effect = generation
        self.clock.set(840)
        first = asyncio.create_task(self.request("generation", sid, preview))
        try:
            await self.admitted()
            await asyncio.wait_for(started.wait(), 3)
            self.assertEqual(self.limiter.borrowed_tokens, 1)
            self.clock.set(960)
            self.assertIsNone(self.store.get(sid))
            for kind in ["generation", "submission", "deletion"]:
                with self.subTest(kind=kind):
                    # Must finish while the sole worker is still blocked.
                    response = await asyncio.wait_for(self.request(kind, sid, preview), 3)
                    self.assert_unavailable(response)
                    self.assertEqual(entry.admitted, 1)
                    self.assertEqual(self.limiter.statistics().tasks_waiting, 0)
            self.jira.create_ticket.assert_not_called()
            self.assertEqual(self.llm.generate_draft.call_count, 1)
        finally:
            release.set()
            response = await asyncio.wait_for(first, 3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(entry.expires_at, 1860)
        self.assertEqual(entry.admitted, 0)

    async def test_preexpiry_admissions_survive_worker_queue_for_all_endpoints(self):
        for kind in ["generation", "submission", "deletion"]:
            with self.subTest(kind=kind):
                base = self.clock()
                sid, preview = self.new_session()
                entry = self.store._sessions[sid]
                holder = object()
                await self.limiter.acquire_on_behalf_of(holder)
                self.clock.set(base + 840)
                task = asyncio.create_task(self.request(kind, sid, preview))
                try:
                    reservation = await self.admitted()
                    self.assertEqual(reservation._state, "pending")
                    self.assertEqual(entry.admitted, 1)
                    self.assertFalse(task.done())
                    self.clock.set(base + 960)
                    # Earlier subcases may leave other sessions eligible for
                    # cleanup; only this admitted entry must remain pinned.
                    self.store.cleanup_expired()
                    self.assertIs(self.store._sessions.get(sid), entry)
                    self.assertEqual(entry.admitted, 1)
                finally:
                    self.limiter.release_on_behalf_of(holder)
                    response = await asyncio.wait_for(task, 3)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(reservation._state, "released")
                self.assertEqual(entry.admitted, 0)
                if kind == "generation":
                    self.assertEqual(entry.expires_at, base + 1860)
                else:
                    self.assertNotIn(sid, self.store._sessions)

    async def test_cancellation_releases_unused_admission_for_all_endpoints(self):
        for kind in ["generation", "submission", "deletion"]:
            with self.subTest(kind=kind):
                base = self.clock()
                sid, preview = self.new_session()
                entry = self.store._sessions[sid]
                holder = object()
                await self.limiter.acquire_on_behalf_of(holder)
                task = asyncio.create_task(self.request(kind, sid, preview))
                try:
                    reservation = await self.admitted()
                    self.assertEqual(reservation._state, "pending")
                    self.clock.set(base + 960)
                    self.assertEqual(self.store.cleanup_expired(), 0)
                    await self.cancel_request(task)
                    self.assertEqual(reservation._state, "released")
                    self.assertEqual(entry.admitted, 0)
                    self.assertNotIn(sid, self.store._sessions)
                    reservation.cancel()  # Repeated cleanup is harmless.
                    self.assertEqual(entry.admitted, 0)
                finally:
                    self.limiter.release_on_behalf_of(holder)
        self.llm.generate_draft.assert_not_called()
        self.jira.create_ticket.assert_not_called()

    async def exercise_running_cancellation(self, *, fail):
        sid, preview = self.new_session()
        entry = self.store._sessions[sid]
        started = asyncio.Event()
        release = Event()
        completed = self.observe_completion("generate_draft")

        def generation(text, template):
            self.loop.call_soon_threadsafe(started.set)
            if not release.wait(3):
                raise AssertionError("Generation was not released")
            if fail:
                raise ValueError("Synthetic generation failure")
            return LLMClient().generate_draft(text, template)

        self.llm.generate_draft.side_effect = generation
        self.clock.set(840)
        task = asyncio.create_task(self.request("generation", sid, preview))
        try:
            reservation = await self.admitted()
            await asyncio.wait_for(started.wait(), 3)
            self.clock.set(960)
            await self.cancel_request(task)
            self.assertEqual(reservation._state, "running")
            self.assertEqual(entry.admitted, 1)
            self.assertEqual(self.store.cleanup_expired(), 0)
            self.assertIn(sid, self.store._sessions)
        finally:
            release.set()
            await asyncio.wait_for(completed.wait(), 3)
        self.assertEqual(reservation._state, "released")
        self.assertEqual(entry.admitted, 0)
        if fail:
            self.assertNotIn(sid, self.store._sessions)
        else:
            self.assertEqual(entry.expires_at, 1860)
            self.assertIsNotNone(self.store.get(sid))
        reservation.cancel()
        self.assertEqual(entry.admitted, 0)

    async def test_cancellation_of_running_success_keeps_worker_pin_until_commit(self):
        await self.exercise_running_cancellation(fail=False)

    async def test_cancellation_of_running_failure_does_not_revive_session(self):
        await self.exercise_running_cancellation(fail=True)

    async def test_cancelled_worker_waiting_for_session_lock_keeps_ownership(self):
        self.limiter.total_tokens = 2
        sid, preview = self.new_session()
        entry = self.store._sessions[sid]
        started = asyncio.Event()
        waiting = asyncio.Event()
        release = Event()
        deletion_completed = self.observe_completion("delete_session")
        original_lock = entry.lock

        class ObservedLock:
            def acquire(inner, blocking=True):
                if blocking and original_lock.locked():
                    self.loop.call_soon_threadsafe(waiting.set)
                return original_lock.acquire(blocking=blocking)

            def release(inner):
                original_lock.release()

        entry.lock = ObservedLock()

        def generation(text, template):
            self.loop.call_soon_threadsafe(started.set)
            if not release.wait(3):
                raise AssertionError("Generation was not released")
            return LLMClient().generate_draft(text, template)

        self.llm.generate_draft.side_effect = generation
        first = asyncio.create_task(self.request("generation", sid, preview))
        second = None
        try:
            await self.admitted()
            await asyncio.wait_for(started.wait(), 3)
            second = asyncio.create_task(self.request("deletion", sid, preview))
            reservation = await self.admitted()
            await asyncio.wait_for(waiting.wait(), 3)
            self.clock.set(960)
            await self.cancel_request(second)
            self.assertEqual(reservation._state, "running")
            self.assertEqual(entry.admitted, 2)
            self.assertEqual(self.store.cleanup_expired(), 0)
        finally:
            release.set()
            await asyncio.wait_for(first, 3)
            if second is not None:
                await asyncio.wait_for(deletion_completed.wait(), 3)
        self.assertEqual(entry.admitted, 0)
        self.assertNotIn(sid, self.store._sessions)

    async def test_cancellation_before_worker_claim_forbids_abandoned_execution(self):
        sid, preview = self.new_session()
        entry = self.store._sessions[sid]
        dispatched = asyncio.Event()
        completed = asyncio.Event()
        release = Event()
        original = self.orchestrator.generate_draft

        def before_claim(*args, **kwargs):
            # Thread exists, but has not entered the production session operation.
            self.loop.call_soon_threadsafe(dispatched.set)
            try:
                if not release.wait(3):
                    raise AssertionError("Worker was not released")
                return original(*args, **kwargs)
            finally:
                self.loop.call_soon_threadsafe(completed.set)

        self.orchestrator.generate_draft = before_claim
        task = asyncio.create_task(self.request("generation", sid, preview))
        try:
            reservation = await self.admitted()
            await asyncio.wait_for(dispatched.wait(), 3)
            self.assertEqual(reservation._state, "pending")
            self.clock.set(960)
            await self.cancel_request(task)
            self.assertEqual(reservation._state, "released")
            self.assertEqual(entry.admitted, 0)
            self.assertNotIn(sid, self.store._sessions)
        finally:
            release.set()
            await asyncio.wait_for(completed.wait(), 3)
        self.llm.generate_draft.assert_not_called()
        self.assertEqual(entry.admitted, 0)
        self.assertNotIn(sid, self.store._sessions)

    async def test_api_concurrent_submissions_consume_only_once(self):
        sid, preview = self.new_session()
        entry = self.store._sessions[sid]
        started = asyncio.Event()
        release = Event()
        adapter = JiraAdapter()

        def submit(fields, template):
            self.loop.call_soon_threadsafe(started.set)
            if not release.wait(3):
                raise AssertionError("Submission was not released")
            return adapter.create_ticket(fields, template)

        self.jira.create_ticket.side_effect = submit
        self.clock.set(840)
        first = asyncio.create_task(self.request("submission", sid, preview))
        second = None
        try:
            first_reservation = await self.admitted()
            await asyncio.wait_for(started.wait(), 3)
            second = asyncio.create_task(self.request("submission", sid, preview))
            second_reservation = await self.admitted()
            self.assertEqual(entry.admitted, 2)
            self.assertEqual(second_reservation._state, "pending")
            self.clock.set(960)
            self.assertEqual(self.store.cleanup_expired(), 0)
        finally:
            release.set()
            first_response = await asyncio.wait_for(first, 3)
            if second is not None:
                second_response = await asyncio.wait_for(second, 3)
        self.assertEqual(first_response.status_code, 200)
        self.assert_unavailable(second_response)
        self.assertEqual(len(adapter._tickets), 1)
        self.assertEqual(self.jira.create_ticket.call_count, 1)
        self.assertEqual(entry.admitted, 0)
        self.assertEqual(first_reservation._state, "released")
        self.assertEqual(second_reservation._state, "released")
        self.assertNotIn(sid, self.store._sessions)

    async def test_dispatch_failure_releases_unused_reservation_without_refresh(self):
        sid, preview = self.new_session()
        entry = self.store._sessions[sid]
        self.clock.set(100)

        async def dispatch_failure(*args, **kwargs):
            raise RuntimeError("Synthetic dispatch failure")

        with patch("app.main.to_thread.run_sync", dispatch_failure):
            response = await self.request("generation", sid, preview)
        reservation = await self.admitted()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Processing failed"})
        self.assertEqual(reservation._state, "released")
        self.assertEqual(entry.admitted, 0)
        self.assertEqual(entry.expires_at, 900)
        self.llm.generate_draft.assert_not_called()

    async def test_cancelled_reservation_is_terminal_and_cannot_be_reused(self):
        sid, _ = self.new_session()
        entry = self.store._sessions[sid]
        reservation = self.store.admit(sid)
        reservation.cancel()
        reservation.cancel()
        with self.assertRaises(SessionUnavailableError):
            with self.store.operation(sid, reservation=reservation):
                self.fail("Cancelled reservation executed")
        self.assertEqual(entry.admitted, 0)
        self.assertEqual(entry.expires_at, 900)

    async def test_queued_request_cannot_use_session_consumed_before_worker_execution(self):
        sid, preview = self.new_session()
        entry = self.store._sessions[sid]
        holder = object()
        await self.limiter.acquire_on_behalf_of(holder)
        task = asyncio.create_task(self.request("submission", sid, preview))
        try:
            reservation = await self.admitted()
            # Exercise real consumption while the API request is capacity-queued.
            with self.store.operation(sid, consume=True):
                pass
            self.assertNotIn(sid, self.store._sessions)
        finally:
            self.limiter.release_on_behalf_of(holder)
            response = await asyncio.wait_for(task, 3)
        self.assert_unavailable(response)
        self.assertEqual(reservation._state, "released")
        self.assertEqual(entry.admitted, 0)
        self.jira.create_ticket.assert_not_called()


if __name__ == "__main__":
    unittest.main()
