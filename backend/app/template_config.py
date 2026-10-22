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
    fields: list[TemplateField]


BUG_TEMPLATE = Template(
    mode="bug",
    fields=[
        TemplateField(
            key="title",
            label="Название",
            required=True,
            hint="Суть бага одной фразой, например: Не открывается обмен при смене даты и времени",
        ),
        TemplateField(
            key="preconditions",
            label="Предусловия",
            required=True,
            hint="Что должно быть настроено или выполнено до начала шагов",
        ),
        TemplateField(
            key="steps",
            label="Шаги воспроизведения",
            required=True,
            hint="Пронумерованные действия, которые приводят к багу",
        ),
        TemplateField(
            key="actual_result",
            label="Фактический результат",
            required=True,
            hint="Что происходит на самом деле",
        ),
        TemplateField(
            key="expected_result",
            label="Ожидаемый результат",
            required=True,
            hint="Что должно происходить",
        ),
        TemplateField(
            key="context",
            label="Контекст",
            required=False,
            hint="Дополнительные сведения: окружение, версия, ссылки, комментарий разработчика",
        ),
    ],
)

TEMPLATES = {BUG_TEMPLATE.mode: BUG_TEMPLATE}


def get_template(mode: str) -> Template:
    return TEMPLATES[mode]

