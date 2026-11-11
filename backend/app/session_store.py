from copy import deepcopy
import re
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from typing import Callable, Iterator

from app.masking import MaskingContext


SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{22}$"


class InvalidSessionIdError(Exception):
    pass


class SessionUnavailableError(Exception):
    pass


@dataclass
class SessionContext(MaskingContext):
    refinement: dict = field(default_factory=dict)


def copy_context(context: MaskingContext) -> SessionContext:
    return SessionContext(context.enabled_keys, context.issued.copy(),
                          deepcopy(getattr(context, 'refinement', {})))


@dataclass
class SessionEntry:
    expires_at: float
    context: MaskingContext
    lock: Lock = field(default_factory=Lock)
    admitted: int = 0  # Executing and waiting operations with frozen eligibility.


class SessionReservation:
    """One admitted operation; ownership transitions under the registry lock.

    pending: the API can cancel unused admission; running: the worker owns it
    (including while waiting for the session lock); released: terminal state.
    """
    def __init__(self, store, session_id: str, entry: SessionEntry):
        self._store = store
        self._session_id = session_id
        self._entry = entry
        self._state = "pending"

    def cancel(self) -> None:
        """Release only unused admission. Never unpin a running worker."""
        with self._store._registry_lock:
            if self._state == "pending":
                self._store._release(self)


class SessionStore:
    def __init__(self, ttl_seconds: int = 900, clock: Callable[[], float] = time.monotonic):
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._sessions: dict[str, SessionEntry] = {}
        self._registry_lock = Lock()

    @staticmethod
    def validate_id(session_id: str) -> None:
        if not isinstance(session_id, str) or re.fullmatch(SESSION_ID_PATTERN, session_id) is None:
            raise InvalidSessionIdError("Invalid request")

    def create(self, context: MaskingContext) -> str:
        with self._registry_lock:
            while True:
                session_id = secrets.token_urlsafe(16)
                if session_id not in self._sessions:
                    break
            self._sessions[session_id] = SessionEntry(
                self._clock() + self._ttl_seconds, copy_context(context)
            )
        return session_id

    def get(self, session_id: str) -> MaskingContext | None:
        """Non-refreshing inspection; never expose mutable stored metadata."""
        self.validate_id(session_id)
        with self._registry_lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                return None
            if self._clock() >= entry.expires_at:
                if not entry.admitted:
                    del self._sessions[session_id]
                return None
            return copy_context(entry.context)

    def admit(self, session_id: str) -> SessionReservation:
        """Freeze eligibility before worker scheduling or session-lock waiting."""
        self.validate_id(session_id)
        with self._registry_lock:
            entry = self._sessions.get(session_id)
            if entry is None or self._clock() >= entry.expires_at:
                if entry is not None and not entry.admitted:
                    del self._sessions[session_id]
                raise SessionUnavailableError("Session unavailable")
            entry.admitted += 1
            return SessionReservation(self, session_id, entry)

    def _release(self, reservation: SessionReservation) -> None:
        # Caller holds registry lock; only pending cancellation or worker-finally
        # may release. Terminal reservations are never reused or decremented twice.
        if reservation._state == "released":
            return
        reservation._state = "released"
        entry = reservation._entry
        entry.admitted -= 1
        session_id = reservation._session_id
        if (self._sessions.get(session_id) is entry and not entry.admitted
                and self._clock() >= entry.expires_at):
            del self._sessions[session_id]

    @contextmanager
    def operation(
        self, session_id: str, *, consume: bool = False,
        reservation: SessionReservation | None = None, refresh: bool = True,
    ) -> Iterator[MaskingContext]:
        """Claim admission once, then serialize work without rechecking TTL.

        API callers reserve before offloading. Direct callers reserve here.
        A worker claims ownership before waiting for the entry lock so HTTP
        cancellation cannot unpin work that might still execute or mutate state.
        """
        reservation = reservation if reservation is not None else self.admit(session_id)
        with self._registry_lock:
            if (reservation._store is not self or reservation._session_id != session_id
                    or reservation._state != "pending"):
                raise SessionUnavailableError("Session unavailable")
            reservation._state = "running"
            entry = reservation._entry

        acquired = False
        try:
            entry.lock.acquire()
            acquired = True
            with self._registry_lock:
                if self._sessions.get(session_id) is not entry:
                    raise SessionUnavailableError("Session unavailable")
                working = copy_context(entry.context)
            yield working
            with self._registry_lock:
                if self._sessions.get(session_id) is not entry:
                    raise SessionUnavailableError("Session unavailable")
                if consume:
                    del self._sessions[session_id]
                elif refresh:
                    entry.context = copy_context(working)
                    entry.expires_at = self._clock() + self._ttl_seconds
        finally:
            with self._registry_lock:
                self._release(reservation)
            if acquired:
                entry.lock.release()

    def delete(
        self, session_id: str, *, reservation: SessionReservation | None = None
    ) -> None:
        with self.operation(session_id, consume=True, reservation=reservation):
            pass

    def cleanup_expired(self) -> int:
        """Bounded registry work only; never wait on operation locks."""
        removed = 0
        with self._registry_lock:
            now = self._clock()
            for session_id, entry in list(self._sessions.items()):
                if entry.admitted or not entry.lock.acquire(blocking=False):
                    continue
                try:
                    if self._sessions.get(session_id) is entry and now >= entry.expires_at:
                        del self._sessions[session_id]
                        removed += 1
                finally:
                    entry.lock.release()
        return removed
