import re
import uuid
from dataclasses import dataclass, field

from app.masking_config import MASK_RULES, MaskRule


INVISIBLE_CHARS = ("\u200b", "\u200c", "\u200d", "\ufeff")
LINE_SEPARATORS = ("\r\n", "\r", "\u2028", "\u2029", "\x0b", "\x0c", "\x85")
SPECIAL_SPACES = ("\u00a0", "\u202f", "\u2009", "\u2007")


def normalize_text(text: str) -> str:
    for char in INVISIBLE_CHARS:
        text = text.replace(char, "")
    for separator in LINE_SEPARATORS:
        text = text.replace(separator, "\n")
    for space in SPECIAL_SPACES:
        text = text.replace(space, " ")
    return text


def active_rules(enabled_keys: set[str] | None) -> list[MaskRule]:
    result = []
    for rule in MASK_RULES:
        if rule.locked:
            result.append(rule)
        elif enabled_keys is None:
            if rule.enabled_by_default:
                result.append(rule)
        elif rule.key in enabled_keys:
            result.append(rule)
    return result


# Placeholder syntax is inert: no token ever authorizes restoration.
PLACEHOLDER = re.compile(r"\[[A-Z_]+:[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\]")
RESTORATION_ALLOWLIST: frozenset[str] = frozenset()


@dataclass
class MaskingContext:
    enabled_keys: frozenset[str]
    # Metadata only. No original values (including non-secrets) are retained.
    issued: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SanitizedText:
    text: str


def require_sanitized(value: SanitizedText) -> str:
    if not isinstance(value, SanitizedText):
        raise TypeError("Sanitized model input required")
    return value.text


class Masker:
    def context(self, enabled_keys: set[str] | None = None) -> MaskingContext:
        return MaskingContext(frozenset(rule.key for rule in active_rules(enabled_keys)))

    def mask(
        self, text: str, enabled_keys: set[str] | None = None
    ) -> tuple[str, MaskingContext]:
        context = self.context(enabled_keys)
        return self.sanitize_fields({"text": text}, context)["text"], context

    def sanitize_fields(self, fields: dict[str, str], context: MaskingContext) -> dict[str, str]:
        """One operation: recognize credentials across all fields, retain no originals.

        Temporary value lookup is local to this call. Unlabeled values from another
        request cannot be recognized by remembering secrets, because none are stored.
        """
        result = {key: normalize_text(value) for key, value in fields.items()}
        rules = active_rules(set(context.enabled_keys))
        originals = tuple(result.values())
        by_value: dict[tuple[str, str], str] = {}

        def token(label, value, category):
            identity = (category, value)
            if identity not in by_value:
                while True:
                    candidate = f"[{label}:{uuid.uuid4()}]"
                    if candidate not in context.issued and all(candidate not in text for text in originals):
                        break
                context.issued[candidate] = category
                by_value[identity] = candidate
            return by_value[identity]

        def transform(text, operation):
            # Later rules cannot consume or embed already protected spans.
            pieces = []
            end = 0
            for match in PLACEHOLDER.finditer(text):
                pieces.extend((operation(text[end:match.start()]), match.group()))
                end = match.end()
            pieces.append(operation(text[end:]))
            return "".join(pieces)

        # Collect credential originals before replacing overlapping matches.
        secrets: dict[str, tuple[str, str]] = {}
        for rule in rules:
            if rule.sensitivity != "secret":
                continue
            for pattern in rule.patterns:
                for text in originals:
                    for match in pattern.finditer(text):
                        # An inert token's label is not a new credential label,
                        # including when punctuation follows its closing bracket.
                        if any(span.start() <= match.start() < span.end()
                               for span in PLACEHOLDER.finditer(text)):
                            continue
                        value = match.group("value") if "value" in pattern.groupindex else match.group()
                        if value[:1] in ("\"", "'") and value[-1:] == value[:1]:
                            value = value[1:-1]
                        # A complete inert token is already safe. But a quoted
                        # secret containing a token plus other text is not.
                        if value and not PLACEHOLDER.fullmatch(value):
                            secrets.setdefault(value, (rule.placeholder, rule.key))
        if secrets:
            pattern = re.compile("|".join(re.escape(value) for value in sorted(secrets, key=len, reverse=True)))
            def redact(match):
                value = match.group()
                label, category = secrets[value]
                return token(label, value, category)
            def redact_text(text):
                protected = [(match.start(), match.end()) for match in PLACEHOLDER.finditer(text)]

                def replace_secret(match):
                    if any(start <= match.start() and match.end() <= end for start, end in protected):
                        return match.group()
                    return redact(match)

                return pattern.sub(replace_secret, text)

            result = {key: redact_text(text) for key, text in result.items()}

        for rule in rules:
            if rule.sensitivity == "secret":
                continue
            for pattern in rule.patterns:
                def replace(match):
                    group = "value" if "value" in pattern.groupindex else 0
                    value = match.group(group)
                    start = match.start(group) - match.start()
                    end = match.end(group) - match.start()
                    return match.group()[:start] + token(rule.placeholder, value, rule.key) + match.group()[end:]
                result = {key: transform(text, lambda part: pattern.sub(replace, part)) for key, text in result.items()}
                if rule.learn_values:
                    known = [(value, placeholder) for (category, value), placeholder in by_value.items() if category == rule.key and len(value) >= 3]
                    for value, placeholder in sorted(known, key=lambda item: len(item[0]), reverse=True):
                        learned = re.compile(r"(?<!\w)" + re.escape(value) + r"(?!\w)")
                        result = {key: transform(text, lambda part: learned.sub(lambda match: placeholder, part)) for key, text in result.items()}
        return result

    def for_model(self, fields: dict[str, str], context: MaskingContext) -> dict[str, SanitizedText]:
        return {key: SanitizedText(value) for key, value in self.sanitize_fields(fields, context).items()}

    def restore(self, text: str, context: MaskingContext, approved_ids: set[str]) -> str:
        """Future final-review extension: empty allowlist, no recoverable values.

        A future policy must validate category, session, explicit consent and
        destination, use a single-pass replacement, then recheck credentials.
        """
        if approved_ids:
            raise ValueError("Restoration is not enabled")
        return text
