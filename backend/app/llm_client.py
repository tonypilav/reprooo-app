from app.template_config import Template


class LLMClient:
    def generate_draft(self, masked_text: str, template: Template) -> dict[str, str]:
        draft = {field.key: "" for field in template.fields}
        draft["title"] = "Баг из обсуждения в треде"
        draft["steps"] = "1. (заглушка)"
        draft["actual_result"] = masked_text
        return draft

    