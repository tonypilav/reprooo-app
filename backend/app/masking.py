import re

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


class Masker:
    def mask(
        self, text: str, enabled_keys: set[str] | None = None
    ) -> tuple[str, dict[str, str]]:
        mapping: dict[str, str] = {}
        placeholder_by_value: dict[str, str] = {}
        counters: dict[str, int] = {}

        def make_replacer(label: str):
            def replace(match: re.Match) -> str:
                group = "value" if "value" in match.re.groupindex else 0
                value = match.group(group)
                if value in placeholder_by_value:
                    placeholder = placeholder_by_value[value]
                else:
                    counters[label] = counters.get(label, 0) + 1
                    placeholder = f"[{label}_{counters[label]}]"
                    mapping[placeholder] = value
                    placeholder_by_value[value] = placeholder
                whole = match.group(0)
                start = match.start(group) - match.start()
                end = match.end(group) - match.start()
                return whole[:start] + placeholder + whole[end:]

            return replace

        masked = normalize_text(text)
        for rule in active_rules(enabled_keys):
            for pattern in rule.patterns:
                masked = pattern.sub(make_replacer(rule.placeholder), masked)
                if rule.learn_values:
                    masked = self._mask_known_values(
                        masked, rule.placeholder, mapping, placeholder_by_value
                    )
        return masked, mapping

    def _mask_known_values(
        self,
        text: str,
        label: str,
        mapping: dict[str, str],
        placeholder_by_value: dict[str, str],
    ) -> str:
        prefix = f"[{label}_"
        known = [value for key, value in mapping.items() if key.startswith(prefix)]
        for value in sorted(known, key=len, reverse=True):
            if len(value) < 3:
                continue
            pattern = re.compile(r"(?<!\w)" + re.escape(value) + r"(?!\w)")
            text = pattern.sub(placeholder_by_value[value], text)
        return text

    def unmask(self, text: str, mapping: dict[str, str]) -> str:
        result = text
        
        for placeholder, original in mapping.items():
            result = result.replace(placeholder, original)
        return result
    