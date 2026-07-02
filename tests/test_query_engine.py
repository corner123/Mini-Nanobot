from __future__ import annotations

import asyncio
from pathlib import Path

from mini_nanobot.core.query_engine import QueryEngine
from mini_nanobot.core.state import ToolCall
from mini_nanobot.llm.base import LLMResponse, ScriptedLLM


def test_query_engine_runs_tool_loop(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    llm = ScriptedLLM(
        [
            LLMResponse(tool_calls=[ToolCall("file.list", {"path": "."})]),
            LLMResponse(text="done"),
        ]
    )
    engine = QueryEngine(tmp_path, llm=llm)

    result = asyncio.run(engine.submit_message("list files", max_turns=4))

    assert result.text == "done"
    assert any(event["tool_name"] == "file.list" for event in result.state.tool_events)
    assert engine.checkpoints.load(result.state.session_id) is not None


def test_query_engine_stops_at_max_turns(tmp_path: Path) -> None:
    llm = ScriptedLLM([LLMResponse(tool_calls=[ToolCall("file.list", {"path": "."})])])
    engine = QueryEngine(tmp_path, llm=llm)

    result = asyncio.run(engine.submit_message("loop", max_turns=1))

    assert "max_turns" in result.text
    assert not result.state.completed
