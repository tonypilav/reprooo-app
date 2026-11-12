from app.template_config import Template
from app.limits import valid_draft_size


class InvalidDraftError(ValueError):
    """Safe structural/reference error; never include submitted keys or values."""

    def __init__(self):
        super().__init__("Invalid request")


class Validator:
    def normalize(self, draft: dict[str, str], template: Template) -> dict[str, str]:
        allowed = {field.key for field in template.fields}
        if (not isinstance(draft, dict)
                or any(not isinstance(key, str) or key not in allowed or not isinstance(value, str)
                       for key, value in draft.items())
                or not valid_draft_size(draft)):
            raise InvalidDraftError()
        return {field.key: draft.get(field.key, "") for field in template.fields}

    def find_missing(self, draft: dict[str, str], template: Template) -> list[str]:
        return [field.key for field in template.fields
                if field.required and not draft.get(field.key, "").strip()]
