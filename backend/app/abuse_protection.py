"""Single-process admission and conservative paid-dispatch accounting."""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
import ipaddress
import math
import time

MODEL = 'gpt-5.4-mini-2026-03-17'
OUTPUT_CAPS = {'generation': 4096, 'analysis': 2048, 'refinement': 4096}
# 150k input tokens at $0.75/M; output at $4.50/M. Integer microdollars.
INPUT_RESERVATION = 112500
BUDGET_MICRODOLLARS = 4000000
MAX_OUTBOUND_BYTES = 131072
paid_session = ContextVar('paid_session', default=None)


class ProtectionError(Exception):
    def __init__(self, code, status=503, retry_after=None):
        super().__init__(code)
        self.code, self.status, self.retry_after = code, status, retry_after


class PaidAccount:
    """Owned by a session entry, deliberately outside transactional context."""
    def __init__(self):
        self.calls = 0


class Protection:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = Lock()
        self.identities = {}
        self.dispatches = deque()
        self.active = 0
        self.outstanding = 0
        self.reserved = 0

    def _prune(self, queue, now):
        while queue and queue[0] <= now - 60:
            queue.popleft()

    def rate(self, identity, kind):
        with self.lock:
            now = self.clock()
            for key, buckets in list(self.identities.items()):
                for queue in buckets.values():
                    self._prune(queue, now)
                if not any(buckets.values()):
                    del self.identities[key]
            if identity not in self.identities:
                if len(self.identities) >= 2048:
                    raise ProtectionError('RATE_LIMIT_CAPACITY_EXHAUSTED', retry_after=60)
                self.identities[identity] = {}
            queue = self.identities[identity].setdefault(kind, deque())
            limit = 5 if kind == 'session' else 6
            if len(queue) >= limit:
                raise ProtectionError('RATE_LIMITED', 429, max(1, math.ceil(queue[0] + 60 - now)))
            queue.append(now)

    @contextmanager
    def operation(self):
        with self.lock:
            if self.outstanding >= 16:
                raise ProtectionError('OPERATION_CAPACITY_EXHAUSTED', retry_after=2)
            self.outstanding += 1
        try:
            yield
        finally:
            with self.lock:
                self.outstanding -= 1

    @contextmanager
    def dispatch(self, account, operation):
        if account is None:
            raise ProtectionError('LLM_ACCOUNT_UNAVAILABLE')
        with self.lock:
            now = self.clock()
            self._prune(self.dispatches, now)
            if self.active >= 2:
                raise ProtectionError('LLM_CAPACITY_EXHAUSTED', retry_after=2)
            if len(self.dispatches) >= 30:
                raise ProtectionError('RATE_LIMITED', 429, max(1, math.ceil(self.dispatches[0] + 60 - now)))
            if account.calls >= 12:
                raise ProtectionError('SESSION_LLM_LIMIT_REACHED', 409)
            cost = INPUT_RESERVATION + OUTPUT_CAPS[operation] * 4500 // 1000
            if self.reserved + cost > BUDGET_MICRODOLLARS:
                raise ProtectionError('DEMO_BUDGET_EXHAUSTED')
            self.reserved += cost
            account.calls += 1
            self.dispatches.append(now)
            self.active += 1
        try:
            yield
        finally:
            with self.lock:
                self.active -= 1


protection = Protection()


def trusted_networks(value, production=False):
    if '*' in value:
        raise ValueError('Wildcard proxy trust is forbidden')
    try:
        return tuple(ipaddress.ip_network(v.strip()) for v in value.split(',') if v.strip())
    except ValueError:
        raise ValueError('Invalid trusted proxy configuration') from None


def client_identity(scope, networks=()):
    """Raw ASGI peer; walk XFF right-to-left only through explicitly trusted hops."""
    try:
        peer = ipaddress.ip_address(scope.get('client', ('', 0))[0])
    except (ValueError, TypeError):
        return 'unknown'
    def trusted(address):
        return any(address in network for network in networks)
    headers = [value for key, value in scope.get('headers', []) if key.lower() == b'x-forwarded-for']
    if not trusted(peer) or not headers:
        return str(peer)
    if len(headers) != 1 or len(headers[0]) > 2048:
        return str(peer)
    try:
        chain = [ipaddress.ip_address(part.strip()) for part in headers[0].decode('ascii').split(',')]
        if len(chain) > 32:
            return str(peer)
    except (ValueError, UnicodeError):
        return str(peer)
    for address in reversed(chain):
        if not trusted(peer):
            break
        peer = address
    return str(peer)


class AbuseMiddleware:
    def __init__(self, app, networks=()):
        self.app, self.networks = app, networks

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and scope['method'] == 'POST':
            path = scope['path']
            kind = 'session' if path == '/api/mask-preview' else 'llm' if path in (
                '/api/generate-draft', '/api/clarifications/analyze', '/api/clarifications/refine') else None
            if kind:
                try:
                    protection.rate(client_identity(scope, self.networks), kind)
                except ProtectionError as error:
                    from starlette.responses import JSONResponse
                    await protection_response(error)(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def protection_response(error):
    from starlette.responses import JSONResponse
    headers = {'Retry-After': str(error.retry_after)} if error.retry_after else {}
    return JSONResponse(status_code=error.status,
                        content={'detail': {'code': error.code, 'message': 'Resource limit reached.'}},
                        headers=headers)
