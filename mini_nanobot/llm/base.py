from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from mini_nanobot.core.state import AgentState, Message, ToolCall, Usage


@dataclass(slots=True)
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    raw: Any = None

    @property
    def is_final(self) -> bool:
        return not self.tool_calls


class LLMProvider(ABC):
    name = "base"

    @abstractmethod
    async def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        state: AgentState,
    ) -> LLMResponse:
        raise NotImplementedError


class ScriptedLLM(LLMProvider):
    """Deterministic provider for tests and demos."""

    name = "scripted"

    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)

    async def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        state: AgentState,
    ) -> LLMResponse:
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(text="Done.")


class RuleBasedLLM(LLMProvider):
    """Offline demo provider.

    It is intentionally small: real reasoning is supplied by an API-backed
    provider, while this class lets the framework be tested without network
    access.
    """

    name = "rule-based"

    async def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        state: AgentState,
    ) -> LLMResponse:
        last_user = next((m.content for m in reversed(messages) if m.role == "user" and not m.is_meta), "")
        already_used = {event.get("tool_name") for event in state.tool_events}
        text = last_user.lower()
        if ("git status" in text or "git状态" in last_user) and "git.status" not in already_used:
            return LLMResponse(tool_calls=[ToolCall(name="git.status", args={})])
        if ("列出" in last_user or "list files" in text) and "file.list" not in already_used:
            return LLMResponse(tool_calls=[ToolCall(name="file.list", args={"path": "."})])
        if ("搜索" in last_user or "search" in text) and "search.rg" not in already_used:
            return LLMResponse(tool_calls=[ToolCall(name="search.rg", args={"pattern": "TODO", "path": "."})])
        return LLMResponse(
            text=(
                "Mini-Nanobot offline model finished. "
                "Use an API-backed LLMProvider for autonomous code edits."
            )
        )
