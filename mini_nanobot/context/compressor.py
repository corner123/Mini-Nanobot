from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
import inspect
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
    microcompact_keep_tool_results: int = 5
    cache_reference_ttl_seconds: int = 300

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

    Levels:
    1. Tool result budget trimming happens in StreamingToolExecutor.
    2. History snip removes duplicate tool output and superseded edit attempts.
    3. Microcompact keeps recent tool results and replaces older ones with a
       deletion or cache-reference placeholder.
    4. Context collapse creates a model-facing projection without deleting the
       canonical message history.
    5. Autocompact optionally delegates summary generation to a runtime
       summarizer, falling back to a deterministic summary.
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
        before = self.token_counter.count_messages(state.active_messages())
        if before < self.budget.effective_window * self.budget.history_snip_threshold:
            return CompressionReport(before, before, [])

        await self.hooks.emit(COMPACT_START, {"state": state, "before_tokens": before})
        actions: list[str] = []
        self._history_snip(state, actions)
        self._microcompact(state, actions)

        current = self.token_counter.count_messages(state.active_messages())
        if current >= self.budget.effective_window * self.budget.collapse_threshold:
            self._context_collapse(state, actions)

        current = self.token_counter.count_messages(state.active_messages())
        if current >= self.budget.effective_window * self.budget.autocompact_threshold:
            await self._autocompact(state, actions)

        after = self.token_counter.count_messages(state.active_messages())
        report = CompressionReport(before, after, actions)
        if report.saved_tokens and state.task_budget_remaining is not None:
            state.task_budget_remaining -= before
        await self.hooks.emit(COMPACT_END, {"state": state, "report": report})
        return report

    def _history_snip(self, state: AgentState, actions: list[str]) -> None:
        messages = state.active_messages()
        changed = 0
        duplicate_outputs = self._older_duplicate_tool_indices(messages)
        superseded_edits = self._superseded_edit_indices(messages, state)
        for idx in sorted(duplicate_outputs | superseded_edits):
            message = messages[idx]
            if "[history-snip]" in message.content:
                continue
            if idx in duplicate_outputs:
                message.content = (
                    f"[history-snip] duplicate {message.name or 'tool'} output removed; "
                    "an equivalent result appears later in the conversation."
                )
            else:
                path = self._tool_event_path(message.tool_call_id, state) or "the same target"
                message.content = f"[history-snip] superseded edit attempt removed; latest edit for {path} remains."
            changed += 1

        long_changed = 0
        for message in messages:
            if message.role == "tool" and len(message.content) > 3_000 and "[history-snip]" not in message.content:
                message.content = self._head_tail(message.content, 1_500)
                long_changed += 1

        if changed or long_changed:
            actions.append(f"history_snip:dedupe={changed},long={long_changed}")

    def _microcompact(self, state: AgentState, actions: list[str]) -> None:
        messages = state.active_messages()
        tool_indices = [i for i, m in enumerate(messages) if m.role == "tool"]
        old = tool_indices[: -self.budget.microcompact_keep_tool_results]
        cache_refs = 0
        removed = 0
        for idx in old:
            message = messages[idx]
            if "[microcompact]" not in message.content:
                if self._cache_reference_is_fresh(message):
                    cache_refs += 1
                    message.content = (
                        f"[microcompact cache_reference={self._cache_reference_id(message)}] "
                        "tool result masked from prompt text; server-side cache may reuse original tokens."
                    )
                else:
                    removed += 1
                    artifact = self._tool_event_artifact(message.tool_call_id, state)
                    suffix = f" Artifact: {artifact}" if artifact else ""
                    message.content = f"[microcompact] old tool result removed from prompt.{suffix}"
        if old:
            actions.append(f"microcompact:removed={removed},cache_reference={cache_refs}")

    def _context_collapse(self, state: AgentState, actions: list[str]) -> None:
        messages = state.active_messages()
        if len(messages) <= 8:
            return
        keep_tail = messages[-6:]
        collapsible = [m for m in messages[:-6] if not (m.role == "system")]
        system = [m for m in messages[:-6] if m.role == "system"]
        summary = self._summarize(state, collapsible, "context-collapse")
        state.compacted_summaries.append(summary)
        projection = system + [Message(role="user", content=summary, is_meta=True, name="context-collapse")] + keep_tail
        state.set_context_projection(projection)
        actions.append("context_collapse:projection")

    async def _autocompact(self, state: AgentState, actions: list[str]) -> None:
        if os.environ.get("DISABLE_COMPACT") or os.environ.get("DISABLE_AUTO_COMPACT"):
            return
        if state.query_source in {"session_memory", "compact"}:
            return
        if int(state.metadata.get("autocompact_failures", 0)) >= 3:
            return
        messages = state.active_messages()
        keep_tail = messages[-4:]
        source = messages[:-4]
        summary = await self._autocompact_summary(state, source)
        recovered = self._recovery_attachment(state)
        state.set_context_projection(
            [
                Message(role="user", content=summary, is_meta=True, name="autocompact"),
                Message(role="user", content=recovered, is_meta=True, name="recovery"),
                *keep_tail,
            ]
        )
        state.compacted_summaries.append(summary)
        source = "summary_agent" if "source=\"summary_agent\"" in summary else "fallback"
        actions.append(f"autocompact:{source}")

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

    def _older_duplicate_tool_indices(self, messages: list[Message]) -> set[int]:
        latest_by_fingerprint: dict[tuple[str | None, str], int] = {}
        duplicates: set[int] = set()
        for idx, message in enumerate(messages):
            if message.role != "tool":
                continue
            fingerprint = (message.name, sha1(message.content.encode("utf-8", errors="replace")).hexdigest())
            if fingerprint in latest_by_fingerprint:
                duplicates.add(latest_by_fingerprint[fingerprint])
            latest_by_fingerprint[fingerprint] = idx
        return duplicates

    def _superseded_edit_indices(self, messages: list[Message], state: AgentState) -> set[int]:
        latest_by_target: dict[str, int] = {}
        superseded: set[int] = set()
        for idx, message in enumerate(messages):
            if message.role != "tool" or message.name not in {"file.write", "file.patch"}:
                continue
            target = self._tool_event_path(message.tool_call_id, state)
            if not target:
                continue
            if target in latest_by_target:
                superseded.add(latest_by_target[target])
            latest_by_target[target] = idx
        return superseded

    def _tool_event_path(self, tool_call_id: str | None, state: AgentState) -> str | None:
        event = self._tool_event(tool_call_id, state)
        args = event.get("args", {}) if event else {}
        path = args.get("path") if isinstance(args, dict) else None
        return str(path) if path else None

    def _tool_event_artifact(self, tool_call_id: str | None, state: AgentState) -> str | None:
        event = self._tool_event(tool_call_id, state)
        artifact = event.get("artifact_path") if event else None
        return str(artifact) if artifact else None

    def _tool_event(self, tool_call_id: str | None, state: AgentState) -> dict | None:
        if not tool_call_id:
            return None
        for event in reversed(state.tool_events):
            if event.get("tool_call_id") == tool_call_id:
                return event
        return None

    def _cache_reference_is_fresh(self, message: Message) -> bool:
        try:
            created = datetime.fromisoformat(message.created_at)
        except ValueError:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - created
        return age.total_seconds() <= self.budget.cache_reference_ttl_seconds

    def _cache_reference_id(self, message: Message) -> str:
        if message.tool_call_id:
            return message.tool_call_id
        return sha1(message.content.encode("utf-8", errors="replace")).hexdigest()[:12]

    async def _autocompact_summary(self, state: AgentState, messages: list[Message]) -> str:
        summarizer = state.metadata.get("compact_summarizer")
        if summarizer is not None:
            try:
                value = summarizer(state, messages, "autocompact")
                if inspect.isawaitable(value):
                    value = await value
                if isinstance(value, str) and value.strip():
                    return value
            except Exception:
                state.metadata["autocompact_failures"] = int(state.metadata.get("autocompact_failures", 0)) + 1
        await asyncio.sleep(0)
        return self._summarize(state, messages, "autocompact")


def store_blob_if_large(text: str, artifact_dir: Path, name: str, max_chars: int = 25_000) -> tuple[str, str | None]:
    if len(text) <= max_chars:
        return text, None
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / name
    path.write_text(text, encoding="utf-8")
    preview = text[:2_000] + "\n...[stored full content on disk]...\n" + text[-2_000:]
    return f"{preview}\n\nFull content: {path}", str(path)
