from __future__ import annotations

import asyncio
from pathlib import Path

from mini_nanobot.context.compressor import ContextBudget, ContextCompressor
from mini_nanobot.context.tokenizer import TokenCounter
from mini_nanobot.core.state import AgentState, Message
from mini_nanobot.memory.checkpoint import SQLiteCheckpointStore


def test_checkpoint_ignores_runtime_metadata(tmp_path: Path) -> None:
    state = AgentState(task="demo")
    state.metadata["runtime_object"] = object()
    state.add_message(Message(role="user", content="hello"))
    store = SQLiteCheckpointStore(tmp_path / "checkpoints.sqlite3")

    store.save(state)
    loaded = store.load(state.session_id)

    assert loaded is not None
    assert loaded.task == "demo"
    assert "runtime_object" not in loaded.metadata


def test_context_compressor_collapses_long_history() -> None:
    state = AgentState(task="compress")
    for i in range(40):
        state.add_message(Message(role="user", content=f"message {i} " + ("x" * 500)))
        state.add_message(Message(role="tool", content="tool output " + ("y" * 1000)))
    compressor = ContextCompressor(TokenCounter(), ContextBudget(max_context_tokens=1200, output_reserve_tokens=100))

    report = asyncio.run(compressor.compress_if_needed(state))

    assert report.actions
    assert report.after_tokens < report.before_tokens
    assert state.compacted_summaries


def test_history_snip_removes_duplicate_outputs_and_superseded_edits() -> None:
    state = AgentState(task="snip")
    state.add_message(Message(role="system", content="system"))
    state.add_message(Message(role="tool", name="file.list", content="same listing", tool_call_id="list_1"))
    state.add_message(Message(role="tool", name="file.list", content="same listing", tool_call_id="list_2"))
    state.add_message(Message(role="tool", name="file.patch", content="patched first", tool_call_id="patch_1"))
    state.add_tool_event({"tool_call_id": "patch_1", "tool_name": "file.patch", "args": {"path": "a.py"}})
    state.add_message(Message(role="tool", name="file.patch", content="patched second", tool_call_id="patch_2"))
    state.add_tool_event({"tool_call_id": "patch_2", "tool_name": "file.patch", "args": {"path": "a.py"}})
    compressor = ContextCompressor(
        TokenCounter(),
        ContextBudget(
            max_context_tokens=40,
            output_reserve_tokens=1,
            collapse_threshold=99,
            autocompact_threshold=99,
            microcompact_keep_tool_results=100,
        ),
    )

    report = asyncio.run(compressor.compress_if_needed(state))

    assert any(action.startswith("history_snip") for action in report.actions)
    assert "duplicate file.list output removed" in state.messages[1].content
    assert "superseded edit attempt removed" in state.messages[3].content


def test_microcompact_uses_cache_reference_for_fresh_tool_results() -> None:
    state = AgentState(task="microcompact")
    state.add_message(Message(role="system", content="system"))
    for i in range(6):
        state.add_message(Message(role="tool", name="search.rg", content=f"unique output {i} " + ("x" * 80), tool_call_id=f"tool_{i}"))
    compressor = ContextCompressor(
        TokenCounter(),
        ContextBudget(
            max_context_tokens=120,
            output_reserve_tokens=1,
            collapse_threshold=99,
            autocompact_threshold=99,
            microcompact_keep_tool_results=2,
        ),
    )

    report = asyncio.run(compressor.compress_if_needed(state))

    assert any(action.startswith("microcompact") for action in report.actions)
    assert "cache_reference=tool_0" in state.messages[1].content
    assert "cache_reference=tool_3" in state.messages[4].content
    assert "unique output 5" in state.messages[-1].content


def test_context_collapse_creates_projection_without_deleting_original_messages() -> None:
    state = AgentState(task="projection")
    state.add_message(Message(role="system", content="system"))
    for i in range(20):
        state.add_message(Message(role="user", content=f"message {i} " + ("x" * 120)))
    original_count = len(state.messages)
    compressor = ContextCompressor(
        TokenCounter(),
        ContextBudget(
            max_context_tokens=120,
            output_reserve_tokens=1,
            collapse_threshold=0.75,
            autocompact_threshold=99,
            microcompact_keep_tool_results=100,
        ),
    )

    report = asyncio.run(compressor.compress_if_needed(state))

    assert "context_collapse:projection" in report.actions
    assert len(state.messages) == original_count
    assert len(state.active_messages()) < len(state.messages)
    assert any(message.name == "context-collapse" for message in state.context_projection)
    assert not any(message.name == "context-collapse" for message in state.messages)
