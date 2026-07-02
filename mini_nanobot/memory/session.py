from __future__ import annotations

from dataclasses import dataclass, field

from mini_nanobot.core.state import Message


@dataclass(slots=True)
class SessionMemory:
    attachments: list[Message] = field(default_factory=list)
    announced_keys: set[str] = field(default_factory=set)

    def add_attachment(self, key: str, content: str) -> Message | None:
        if key in self.announced_keys:
            return None
        self.announced_keys.add(key)
        message = Message(role="user", content=content, is_meta=True, name=key)
        self.attachments.append(message)
        return message
