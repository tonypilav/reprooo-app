import json
from typing import TypeVar

from openai import APIResponseValidationError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from app import config
from app.masking import Masker, SanitizedText, require_sanitized
from app.refinement_schemas import QuestionBatch, ProposalBatch, sanitize_refinement_content

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    def __init__(self, message: str, *, category: str = 'local_validation'):
        super().__init__(message)
        self.category = category if category in (
            'provider_api', 'structured_output', 'local_validation'
        ) else 'local_validation'


class LLMTimeoutError(LLMError):
    pass


class OpenAIClient:
    def __init__(self):
        self._client = None

    def ask(self, system: SanitizedText, user: SanitizedText, schema: type[T]) -> T:
        masker = Masker()
        inputs = {"system": require_sanitized(system), "user": require_sanitized(user)}
        timed_out = False
        category = 'provider_api'
        try:
            context = masker.context(set())
            if schema in (QuestionBatch, ProposalBatch):
                # Recheck content without treating serialized IDs/enums as prose.
                payload = sanitize_refinement_content(masker, context, json.loads(inputs['user']))
                safe = masker.sanitize_fields({'system': inputs['system']}, context)
                safe['user'] = json.dumps(payload, ensure_ascii=False)
            else:
                safe = masker.sanitize_fields(inputs, context)
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
        except (ValidationError, APIResponseValidationError, ValueError, TypeError, KeyError):
            category = 'structured_output'
        except Exception:
            pass
        # Raise outside handlers: retain no sensitive provider/validation context.
        if timed_out:
            raise LLMTimeoutError("Model request timed out", category=category)
        raise LLMError("Model request failed", category=category)
