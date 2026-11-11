from dataclasses import dataclass


@dataclass(frozen=True)
class TemplateField:
    key: str
    label: str
    required: bool
    hint: str


@dataclass(frozen=True)
class Template:
    mode: str
    version: int
    fields: list[TemplateField]


REQUIREMENTS_TEMPLATE = Template(
    mode="requirements",
    version=1,
    fields=[
        TemplateField(
            key="summary",
            label="Краткое описание",
            required=True,
            hint="Какое изменение требуется — кратко и по существу",
        ),
        TemplateField(
            key="business_context",
            label="Бизнес-контекст",
            required=True,
            hint="Какую проблему решаем, для кого и какой результат ожидаем",
        ),
        TemplateField(
            key="requirements",
            label="Требования",
            required=False,
            hint="Ожидаемое поведение, известные ограничения и особые случаи",
        ),
        TemplateField(
            key="acceptance_criteria",
            label="Критерии приёмки",
            required=True,
            hint="Наблюдаемые и проверяемые условия завершения задачи",
        ),
        TemplateField(
            key="open_questions",
            label="Открытые вопросы",
            required=False,
            hint="Недостающая информация, неоднозначности и нерешённые вопросы",
        ),
    ],
)

TEMPLATES = {REQUIREMENTS_TEMPLATE.mode: REQUIREMENTS_TEMPLATE}


def get_template(mode: str) -> Template:
    return TEMPLATES[mode]


def get_active_template() -> Template:
    return REQUIREMENTS_TEMPLATE
