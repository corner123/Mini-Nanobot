from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from mini_nanobot.context.tokenizer import TokenCounter
from mini_nanobot.core.state import AgentState, Message
from mini_nanobot.hooks.manager import COMPACT_END, COMPACT_START, HookManager


@dataclass(slots=True)
class ContextBudget:
    max_context_tokens: int = 32_000
    output_reserve_tokens: int = 4_000
    history_snip_threshold: float = 0.72
    collapse_threshold: float = 0.90
    autocompact_threshold: float = 0.93

    @property
    def effective_window(self) -> int:
        return max(1, self.max_context_tokens - self.output_reserve_tokens)


@dataclass(slots=True)
class CompressionReport:
    before_tokens: int
    after_tokens: int
    actions: list[str]

    @property
    def saved_tokens(self) -> int:
        return max(0, self.before_tokens - self.after_tokens)


class ContextCompressor:
    """Progressive context compressor.

    The implementation mirrors the architecture, not vendor internals:
    tool-result previews, history snip, microcompact, collapse, and a final
    deterministic autocompact fallback.
    """

    def __init__(
        self,
        token_counter: TokenCounter | None = None,
        budget: ContextBudget | None = None,
        hooks: HookManager | None = None,
    ) -> None:
        self.token_counter = token_counter or TokenCounter()
        self.budget = budget or ContextBudget()
        self.hooks = hooks or HookManager()

    async def compress_if_needed(self, state: AgentState) -> CompressionReport:
        before = self.token_counter.count_messages(state.messages)
        if before < self.budget.effective_window * self.budget.history_snip_threshold:
            return CompressionReport(before, before, [])

        await self.hooks.emit(COMPACT_START, {"state": state, "before_tokens": before})
        actions: list[str] = []
        self._history_snip(state, actions)
        self._microcompact(state, actions)

        current = self.token_counter.count_messages(state.messages)
        if current >= self.budget.effective_window * self.budget.collapse_threshold:
            self._context_collapse(state, actions)

        current = self.token_counter.count_messages(state.messages)
        if current >= self.budget.effective_window * self.budget.autocompact_threshold:
            self._autocompact(state, actions)

        after = self.token_counter.count_messages(state.messages)
        report = CompressionReport(before, after, actions)
        if report.saved_tokens and state.task_budget_remaining is not None:
            state.task_budget_remaining -= before
        await self.hooks.emit(COMPACT_END, {"state": state, "report": report})
        return report

    def _history_snip(self, state: AgentState, actions: list[str]) -> None:
        changed = 0
        for message in state.messages:
            if message.role == "tool" and len(message.content) > 3_000:
                message.content = self._head_tail(message.content, 1_500)
                changed += 1
        if changed:
            actions.append(f"history_snip:{changed}")

    def _microcompact(self, state: AgentState, actions: list[str]) -> None:
        tool_indices = [i for i, m in enumerate(state.messages) if m.role == "tool"]
        old = tool_indices[:-8]
        for idx in old:
            message = state.messages[idx]
            if "[microcompact]" not in message.content:
                message.content = "[microcompact] old tool result removed; use artifact path if referenced."
        if old:
            actions.append(f"microcompact:{len(old)}")

    def _context_collapse(self, state: AgentState, actions: list[str]) -> None:
        if len(state.messages) <= 8:
            return
        keep_tail = state.messages[-6:]
        collapsible = [m for m in state.messages[:-6] if not (m.role == "system")]
        system = [m for m in state.messages[:-6] if m.role == "system"]
        summary = self._summarize(state, collapsible, "context-collapse")
        state.compacted_summaries.append(summary)
        state.messages = system + [Message(role="user", content=summary, is_meta=True, name="context-collapse")] + keep_tail
        actions.append("context_collapse")

    def _autocompact(self, state: AgentState, actions: list[str]) -> None:
        if os.environ.get("DISABLE_COMPACT") or os.environ.get("DISABLE_AUTO_COMPACT"):
            return
        if state.query_source in {"session_memory", "compact"}:
            return
        if int(state.metadata.get("autocompact_failures", 0)) >= 3:
            return
        keep_tail = state.messages[-4:]
        summary = self._summarize(state, state.messages[:-4], "autocompact")
        recovered = self._recovery_attachment(state)
        state.messages = [
            Message(role="user", content=summary, is_meta=True, name="autocompact"),
            Message(role="user", content=recovered, is_meta=True, name="recovery"),
            *keep_tail,
        ]
        state.compacted_summaries.append(summary)
        actions.append("autocompact")

    def _summarize(self, state: AgentState, messages: list[Message], mode: str) -> str:
        bullets = []
        for message in messages[-30:]:
            if message.role == "system":
                continue
            bullets.append(f"- {message.role}: {message.short(180)}")
        plan = "\n".join(f"- [{step.status}] {step.text}" for step in state.plan) or "- no active plan"
        return (
            f"<system-reminder name=\"{mode}\">\n"
            "Compressed conversation state. Preserve these facts for continuation.\n\n"
            f"Task: {state.task}\n\n"
            "Plan:\n"
            f"{plan}\n\n"
            "Recent decisions and observations:\n"
            + "\n".join(bullets)
            + "\n</system-reminder>"
        )

    def _recovery_attachment(self, state: AgentState) -> str:
        files = "\n".join(f"- {path}" for path in state.recent_files[:5]) or "- none"
        skills = "\n".join(f"- {name}" for name in state.invoked_skills[-5:]) or "- none"
        return (
            "<system-reminder name=\"post-compact-recovery\">\n"
            "Recent files to verify before trusting stale memory:\n"
            f"{files}\n\n"
            "Recently invoked skills:\n"
            f"{skills}\n"
            "</system-reminder>"
        )

    def _head_tail(self, text: str, limit: int) -> str:
        return text[:limit] + "\n...[snipped]...\n" + text[-limit:]


def store_blob_if_large(text: str, artifact_dir: Path, name: str, max_chars: int = 25_000) -> tuple[str, str | None]:
    if len(text) <= max_chars:
        return text, None
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / name
    path.write_text(text, encoding="utf-8")
    preview = text[:2_000] + "\n...[stored full content on disk]...\n" + text[-2_000:]
    return f"{preview}\n\nFull content: {path}", str(path)
