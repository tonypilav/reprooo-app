import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MaskRule:
    key: str
    label: str
    description: str
    placeholder: str
    patterns: tuple[re.Pattern, ...]
    enabled_by_default: bool = True
    locked: bool = False
    learn_values: bool = False


# Rules run in this order. The order matters: for example, links are masked
# before IP addresses, so an IP inside a URL is hidden together with the URL.
# If a pattern has a group named "value", only that part of the match is masked.
MASK_RULES = [
    MaskRule(
        key="secrets",
        label="Пароли и секреты",
        description="Значения после слов password, token, api_key и подобных",
        placeholder="SECRET",
        patterns=(
            re.compile(
                r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|пароль)\b"
                r"\s*[:=]\s*(?P<value>[^\s,;]+)"
            ),
        ),
        locked=True,
    ),
    MaskRule(
        key="tokens",
        label="Токены и ключи",
        description="JWT, Bearer-токены, ключи API",
        placeholder="TOKEN",
        patterns=(
            re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+"),
            re.compile(r"(?i)\bBearer\s+(?P<value>[\w.~+/=-]{8,})"),
            re.compile(
                r"\b(?:sk-[\w-]{16,}|gh[pousr]_\w{20,}|xox[abprs]-[\w-]{10,}|AKIA[0-9A-Z]{16})"
            ),
        ),
        locked=True,
    ),
    MaskRule(
        key="slack_names",
        label="Имена из Slack",
        description="Авторы сообщений и упоминания @имя",
        placeholder="PERSON",
        patterns=(
            re.compile(
                r"(?m)^(?P<value>[^\n\[\]]+?)[^\S\n]{2,}"
                r"\[\d{1,2}:\d{2}(?:\s?[AP]M)?\][^\S\n]*$"
            ),
            re.compile(r"\[@(?P<value>[^\[\]]+)\]"),
            re.compile(
                r"(?<![\w.@])@(?!(?:here|channel|everyone)\b)"
                r"(?P<value>[\w-]+(?:\.[\w-]+)*)"
            ),
        ),
        learn_values=True,
    ),
    MaskRule(
        key="links",
        label="Ссылки",
        description="Любые адреса вида https://...",
        placeholder="LINK",
        patterns=(re.compile(r"https?://[^\s)\]>]+"),),
    ),
    MaskRule(
        key="emails",
        label="Email",
        description="Адреса электронной почты",
        placeholder="EMAIL",
        patterns=(re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),),
    ),
    MaskRule(
        key="hashes",
        label="Хеши транзакций",
        description="Длинные hex-строки из 64 символов, с 0x или без",
        placeholder="HASH",
        patterns=(re.compile(r"\b(?:0x)?[0-9a-fA-F]{64}\b"),),
    ),
    MaskRule(
        key="wallets",
        label="Адреса кошельков",
        description="Длинные строки из букв и цифр (26-45 символов)",
        placeholder="WALLET",
        patterns=(
            re.compile(
                r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{26,45}\b"
            ),
        ),
    ),
    MaskRule(
        key="exchange_ids",
        label="ID обменов",
        description="Идентификатор после слов for exchange",
        placeholder="EXCHANGE_ID",
        patterns=(
            re.compile(r"(?i)\bfor exchange\s+(?P<value>[A-Za-z0-9]{10,14})\b"),
        ),
    ),
    MaskRule(
        key="ips",
        label="IP-адреса",
        description="IPv4-адреса вида 10.0.0.1",
        placeholder="IP",
        patterns=(re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),),
    ),
    MaskRule(
        key="phones",
        label="Телефоны",
        description="Номера с + или российским префиксом 8",
        placeholder="PHONE",
        patterns=(
            re.compile(
                r"(?<![\w+])(?:\+7|8)[\s(.-]*\d{3}[\s).-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}\b"
                r"|(?<![\w+])\+\d{1,3}[\s(.-]*\d[\d\s().-]{7,}\d"
            ),
        ),
    ),
]