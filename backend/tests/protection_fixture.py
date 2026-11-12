"""Isolated synthetic guards for pre-existing workflow regressions."""
from unittest.mock import patch
from app import abuse_protection, main, openai_client, config


def isolate_protection(case):
    guard = abuse_protection.Protection()
    # Legacy API scenarios exercise workflows, not throttling; dedicated tests
    # below exercise the real rate limiter and middleware.
    guard.rate = lambda *args: None
    for module in (abuse_protection, main, openai_client):
        replacement = patch.object(module, 'protection', guard)
        replacement.start()
        case.addCleanup(replacement.stop)
    model = patch.object(config, 'OPENAI_MODEL', abuse_protection.MODEL)
    model.start()
    case.addCleanup(model.stop)
    token = abuse_protection.paid_session.set(abuse_protection.PaidAccount())
    case.addCleanup(abuse_protection.paid_session.reset, token)
