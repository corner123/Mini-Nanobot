from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from typing import Any, Awaitable, Callable


HookCallback = Callable[[dict[str, Any]], "HookResult | Awaitable[HookResult | None] | None"]


@dataclass(slots=True)
class HookResult:
    allow: bool = True
    reason: str | None = None
    args: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class HookManager:
    """Event bus for agent lifecycle extension points."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[HookCallback]] = {}

    def register(self, event: str, callback: HookCallback) -> None:
        self._listeners.setdefault(event, []).append(callback)

    async def emit(self, event: str, payload: dict[str, Any]) -> list[HookResult]:
        results: list[HookResult] = []
        for callback in self._listeners.get(event, []):
            value = callback(payload)
            if inspect.isawaitable(value):
                value = await value
            if value is None:
                continue
            results.append(value)
            if not value.allow:
                break
        return results

    async def check(self, event: str, payload: dict[str, Any]) -> HookResult:
        merged_args = payload.get("args")
        for result in await self.emit(event, payload):
            if result.args is not None:
                merged_args = result.args
            if not result.allow:
                return HookResult(allow=False, reason=result.reason, args=merged_args)
        return HookResult(allow=True, args=merged_args)


PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"
SESSION_START = "SessionStart"
SESSION_END = "SessionEnd"
COMPACT_START = "CompactStart"
COMPACT_END = "CompactEnd"
