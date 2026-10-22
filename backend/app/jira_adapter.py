from dataclasses import dataclass

from app.template_config import Template


@dataclass(frozen=True)
class CreatedTicket:
    key: str
    url: str
    summary: str
    description: str


class JiraAdapter:
    def __init__(self):
        self._tickets: list[CreatedTicket] = []

    def create_ticket(self, draft: dict[str, str], template: Template) -> CreatedTicket:
        summary = draft.get("title", "").strip()

        parts = []
        for field in template.fields:
            if field.key == "title":
                continue
            value = draft.get(field.key, "").strip()
            if value:
                parts.append(f"{field.label}:\n{value}")
        description = "\n\n".join(parts)

        key = f"MOCK-{len(self._tickets) + 1}"
        ticket = CreatedTicket(
            key=key,
            url=f"https://jira.example.com/browse/{key}",
            summary=summary,
            description=description,
        )
        self._tickets.append(ticket)
        return ticket