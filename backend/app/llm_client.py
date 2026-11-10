from app.template_config import Template
from app.masking import SanitizedText, require_sanitized


class LLMClient:
    def generate_draft(self, masked_text: SanitizedText, template: Template) -> dict[str, str]:
        draft = {field.key: "" for field in template.fields}
        draft["title"] = "Баг из обсуждения в треде"
        draft["steps"] = "1. (заглушка)"
        draft["actual_result"] = require_sanitized(masked_text)
        return draft

    