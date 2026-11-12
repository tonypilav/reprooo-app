import ipaddress
import os
import re
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "stub")


def validate_llm_config():
    if LLM_PROVIDER not in {"stub", "openai"}:
        raise ValueError("LLM_PROVIDER must be stub or openai")
    if LLM_PROVIDER == "openai" and not OPENAI_API_KEY.strip():
        raise ValueError("OPENAI_API_KEY is required for openai mode")


def get_cors_origins():
    """Validate before application initialization; never echo environment values."""
    environment = os.getenv("APP_ENV", "development").strip()
    if environment not in {"development", "production"}:
        raise ValueError("APP_ENV must be development or production")
    configured = os.getenv("CORS_ALLOWED_ORIGINS")
    if configured is None and environment == "development":
        return ["http://localhost:5173"]
    origins = configured.split(",") if configured is not None else []
    invalid = ValueError("CORS_ALLOWED_ORIGINS must contain explicit valid origins (HTTPS in production)")
    if not origins:
        raise invalid
    result = []
    for value in origins:
        origin = value.strip()
        try:
            url = urlsplit(origin)
            _ = url.port  # Validate malformed/out-of-range ports.
            if (not origin or any(c.isspace() for c in origin)
                    or any(c in origin for c in "\\?#*")
                    or url.scheme not in {"http", "https"} or not url.hostname
                    or url.username is not None or url.password is not None
                    or url.path or url.query or url.fragment
                    or origin != f"{url.scheme}://{url.netloc}"
                    or url.netloc.endswith(":")
                    or (environment == "production" and (
                        url.scheme != "https" or url.hostname in {"localhost", "127.0.0.1", "::1"}))):
                raise invalid
            # Hostnames must be DNS-style labels or valid IP literals.
            try:
                ipaddress.ip_address(url.hostname)
            except ValueError:
                if not all(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                           for label in url.hostname.split(".")):
                    raise invalid
        except ValueError:
            raise invalid from None
        if origin not in result:
            result.append(origin)
    return result
