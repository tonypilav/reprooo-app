import secrets
import time


class SessionStore:
    def __init__(self, ttl_seconds: int = 900):
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, tuple[float, dict[str, str]]] = {}

    def create(self, mapping: dict[str, str]) -> str:
        session_id = secrets.token_urlsafe(16)
        expires_at = time.monotonic() + self._ttl_seconds
        self._sessions[session_id] = (expires_at, mapping)
        return session_id

    def get(self, session_id: str) -> dict[str, str] | None:
        entry = self._sessions.get(session_id)
        if entry is None:
            return None
        expires_at, mapping = entry
        if time.monotonic() > expires_at:
            del self._sessions[session_id]
            return None
        return mapping

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        