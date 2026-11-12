from dataclasses import dataclass
from threading import Lock

from app.config import get_storage_limit
from app.limits import CapacityError

from app.template_config import Template
from app.masking import Masker
from app.validation import Validator


@dataclass(frozen=True)
class CreatedTicket:
    key: str
    url: str
    summary: str
    description: str


class JiraAdapter:
    def __init__(self):
        self._tickets: list[CreatedTicket] = []
        self._max_tickets = get_storage_limit("MAX_MOCK_TICKETS", 100)
        self._lock = Lock()

    def create_ticket(self, draft: dict[str, str], template: Template) -> CreatedTicket:
        draft = Validator().normalize(draft, template)
        summary = draft["summary"].strip()

        parts = []
        for field in template.fields:
            if field.key == "summary":
                continue
            value = draft.get(field.key, "").strip()
            if value:
                parts.append(f"{field.label}:\n{value}")
        description = "\n\n".join(parts)

        masker = Masker()
        safe = masker.sanitize_fields({"summary": summary, "description": description}, masker.context(set()))
        summary, description = safe["summary"], safe["description"]

        with self._lock:
            if len(self._tickets) >= self._max_tickets:
                raise CapacityError()
            key = f"MOCK-{len(self._tickets) + 1}"
            ticket = CreatedTicket(
                key=key,
                url=f"https://jira.example.com/browse/{key}",
                summary=summary,
                description=description,
            )
            self._tickets.append(ticket)
            return ticket
