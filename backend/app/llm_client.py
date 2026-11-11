from app.template_config import Template
from app.masking import SanitizedText, require_sanitized


class LLMClient:
    def generate_draft(self, masked_text: SanitizedText, template: Template) -> dict[str, str]:
        text = require_sanitized(masked_text)
        draft = {field.key: "" for field in template.fields}
        draft["summary"] = next((line for line in text.splitlines() if line.strip()), "")
        draft["requirements"] = text
        return draft
