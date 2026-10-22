from app.template_config import Template


class Validator:
    def find_missing(self, draft: dict[str, str], template: Template) -> list[str]:
        missing = []
        for field in template.fields:
            if not field.required:
                continue
            value = draft.get(field.key, "")
            if not value.strip():
                missing.append(field.key)
        return missing