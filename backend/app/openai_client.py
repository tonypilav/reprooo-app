from typing import TypeVar

from openai import OpenAI, OpenAIError
from pydantic import BaseModel

from app import config
from app.masking import Masker, SanitizedText, require_sanitized

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class OpenAIClient:
    def __init__(self):
        self._client = None

    def ask(self, system: SanitizedText, user: SanitizedText, schema: type[T]) -> T:
        masker = Masker()
        safe = masker.sanitize_fields(
            {"system": require_sanitized(system), "user": require_sanitized(user)},
            masker.context(set()),
        )
        if self._client is None:
            if not config.OPENAI_API_KEY:
                raise LLMError("OPENAI_API_KEY is not set")
            self._client = OpenAI(api_key=config.OPENAI_API_KEY, timeout=60)
        try:
            response = self._client.responses.parse(
                model=config.OPENAI_MODEL,
                input=[
                    {"role": "system", "content": safe["system"]},
                    {"role": "user", "content": safe["user"]},
                ],
                text_format=schema,
                store=False,
            )
        except OpenAIError:
            pass
        else:
            if response.output_parsed is None:
                raise LLMError("The model returned no structured output")
            return response.output_parsed
        # Raise outside the handler: do not retain provider text in exception context.
        raise LLMError("Model request failed")
