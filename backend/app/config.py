import os

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
