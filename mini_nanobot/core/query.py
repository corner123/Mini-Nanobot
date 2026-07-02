from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from mini_nanobot.context.compressor import ContextCompressor
from mini_nanobot.core.state import AgentState, Message, QueryEvent
from mini_nanobot.hooks.manager import HookManager
from mini_nanobot.llm.base import LLMProvider
from mini_nanobot.memory.checkpoint import SQLiteCheckpointStore
from mini_nanobot.tools.base import PermissionLevel, ToolContext
from mini_nanobot.tools.executor import StreamingToolExecutor
from mini_nanobot.tools.registry import ToolRegistry


async def query(
    state: AgentState,
    llm: LLMProvider,
    registry: ToolRegistry,
    workspace: Path,
    checkpoint: SQLiteCheckpointStore,
    compressor: ContextCompressor,
    hooks: HookManager,
    permissions: set[PermissionLevel],
    max_turns: int = 20,
) -> list[QueryEvent]:
    """Single user-query loop.

    The outer QueryEngine owns session lifecycle; this function owns one
    resumable ReAct loop and saves checkpoints after every significant step.
    """

    events: list[QueryEvent] = []
    artifact_dir = workspace / ".nanobot" / "artifacts" / state.session_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    tool_ctx = ToolContext(
        workspace=workspace,
        session_id=state.session_id,
        artifact_dir=artifact_dir,
        permissions=permissions,
        metadata=state.metadata,
    )
    executor = StreamingToolExecutor(registry, hooks)

    while not state.completed and state.turns < max_turns:
        report = await compressor.compress_if_needed(state)
        if report.actions:
            events.append(QueryEvent("context.compacted", {"actions": report.actions, "saved_tokens": report.saved_tokens}))

        state.turns += 1
        events.append(QueryEvent("llm.start", {"turn": state.turns}))
        response = await llm.generate(state.messages, registry.to_model_tools(), state)
        state.usage.add(response.usage)

        if response.text:
            state.add_message(Message(role="assistant", content=response.text))

        if response.tool_calls:
            state.add_message(
                Message(
                    role="assistant",
                    content=json.dumps([asdict(call) for call in response.tool_calls], ensure_ascii=False),
                    name="tool_calls",
                )
            )
            checkpoint.save(state)
            events.append(QueryEvent("tool.batch_start", {"count": len(response.tool_calls)}))
            results = await executor.execute_many(response.tool_calls, tool_ctx)
            for call, result in results:
                if result.context_updates.get("recent_file"):
                    state.remember_file(result.context_updates["recent_file"])
                state.add_tool_event(
                    {
                        "tool_call_id": call.id,
                        "tool_name": call.name,
                        "args": call.args,
                        "is_error": result.is_error,
                        "artifact_path": result.artifact_path,
                    }
                )
                state.add_message(
                    Message(
                        role="tool",
                        content=result.content,
                        name=call.name,
                        tool_call_id=call.id,
                    )
                )
                events.append(
                    QueryEvent(
                        "tool.result",
                        {
                            "tool": call.name,
                            "is_error": result.is_error,
                            "artifact_path": result.artifact_path,
                        },
                    )
                )
            checkpoint.save(state)
            continue

        state.completed = True
        state.final_response = response.text
        checkpoint.save(state)
        events.append(QueryEvent("llm.final", {"text": response.text}))
        break

    if not state.completed:
        state.final_response = "Stopped before completion: max_turns reached."
        checkpoint.save(state)
        events.append(QueryEvent("query.stopped", {"reason": "max_turns"}))
    return events
