import re

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


class Masker:
    def mask(self, text: str) -> tuple[str, dict[str, str]]:
        mapping = {}
        masked = text

        emails = EMAIL_PATTERN.findall(text)
        unique_emails = list(dict.fromkeys(emails))

        for number, email in enumerate(unique_emails, start=1):
            placeholder = f"[EMAIL_{number}]"
            mapping[placeholder] = email
            masked = masked.replace(email, placeholder)

        return masked, mapping

    def unmask(self, text: str, mapping: dict[str, str]) -> str:
        result = text
        
        for placeholder, original in mapping.items():
            result = result.replace(placeholder, original)
        return result