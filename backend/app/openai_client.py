from typing import TypeVar

from openai import APITimeoutError, OpenAI
from pydantic import BaseModel

from app import config
from app.masking import Masker, SanitizedText, require_sanitized

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class LLMTimeoutError(LLMError):
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
        timed_out = False
        try:
            if self._client is None:
                self._client = OpenAI(api_key=config.OPENAI_API_KEY, timeout=60, max_retries=0)
            response = self._client.responses.parse(
                model=config.OPENAI_MODEL,
                input=[
                    {"role": "system", "content": safe["system"]},
                    {"role": "user", "content": safe["user"]},
                ],
                text_format=schema,
                store=False,
            )
            if response.status != "completed" or any(
                content.type == "refusal"
                for item in response.output if item.type == "message"
                for content in item.content
            ) or response.output_parsed is None:
                raise ValueError("Invalid model response")
            return schema.model_validate(response.output_parsed.model_dump())
        except APITimeoutError:
            timed_out = True
        except Exception:
            pass
        # Raise outside handlers: retain no sensitive provider/validation context.
        if timed_out:
            raise LLMTimeoutError("Model request timed out")
        raise LLMError("Model request failed")
