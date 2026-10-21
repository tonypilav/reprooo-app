class LLMClient:
    def generate_draft(self, masked_text: str) -> str:
        return (
            "Заголовок: Баг из обсуждения в треде\n"
            "\n"
            "Описание:\n"
            f"{masked_text}\n"
            "\n"
            "Шаги воспроизведения: (заглушка)\n"
            "Ожидаемый результат: (заглушка)\n"
            "Фактический результат: (заглушка)\n"
        )
    