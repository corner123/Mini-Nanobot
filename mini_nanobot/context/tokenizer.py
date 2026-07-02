from __future__ import annotations

from functools import lru_cache

from mini_nanobot.core.state import Message


@lru_cache(maxsize=8)
def _encoding(model: str):
    try:
        import tiktoken

        return tiktoken.encoding_for_model(model)
    except Exception:
        return None


class TokenCounter:
    def __init__(self, model: str = "gpt-4.1-mini") -> None:
        self.model = model

    def count_text(self, text: str) -> int:
        enc = _encoding(self.model)
        if enc is None:
            return max(1, len(text) // 4)
        return len(enc.encode(text))

    def count_messages(self, messages: list[Message]) -> int:
        total = 0
        for message in messages:
            total += 4
            total += self.count_text(message.role)
            total += self.count_text(message.name or "")
            total += self.count_text(message.content)
        return total
