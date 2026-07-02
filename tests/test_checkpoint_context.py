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
