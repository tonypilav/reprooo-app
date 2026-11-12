"""Production logs contain allowlisted diagnostics, never transport payloads."""
import logging


class SafeServerError(logging.Filter):
    def filter(self, record):
        # Uvicorn may attach request paths or arbitrary exception messages.
        record.msg = 'ASGI server diagnostic'
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def configure_safe_logging():
    # Defense in depth even when the operator forgets --no-access-log.
    for name in ('uvicorn.access', 'openai', 'httpx', 'httpx2', 'httpcore', 'httpcore2'):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.disabled = True
        logger.propagate = False
    logging.getLogger('uvicorn.error').setLevel(logging.WARNING)
    server = logging.getLogger('uvicorn.error')
    if not any(isinstance(f, SafeServerError) for f in server.filters):
        server.addFilter(SafeServerError())
    logging.getLogger('asyncio').setLevel(logging.WARNING)
