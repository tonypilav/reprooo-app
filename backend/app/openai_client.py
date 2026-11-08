from typing import TypeVar

from openai import OpenAI, OpenAIError
from pydantic import BaseModel

from app import config

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class OpenAIClient:
    def __init__(self):
        self._client = None

    def ask(self, system: str, user: str, schema: type[T]) -> T:
        if self._client is None:
            if not config.OPENAI_API_KEY:
                raise LLMError("OPENAI_API_KEY is not set")
            self._client = OpenAI(api_key=config.OPENAI_API_KEY, timeout=60)
        try:
            response = self._client.responses.parse(
                model=config.OPENAI_MODEL,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                text_format=schema,
                store=False,
            )
        except OpenAIError as error:
            raise LLMError(str(error)) from error
        if response.output_parsed is None:
            raise LLMError("The model returned no structured output")
        return response.output_parsed