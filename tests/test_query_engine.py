from __future__ import annotations

import asyncio
from pathlib import Path

from mini_nanobot.core.query_engine import QueryEngine
from mini_nanobot.core.state import ToolCall
from mini_nanobot.llm.base import LLMResponse, ScriptedLLM
from mini_nanobot.tools.base import ToolContext


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


def test_agent_run_executes_child_agent(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    llm = ScriptedLLM(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        "agent.run",
                        {
                            "description": "inspect workspace files",
                            "prompt": "list files in the delegated workspace",
                            "subagent_type": "researcher",
                        },
                    )
                ]
            ),
            LLMResponse(tool_calls=[ToolCall("file.list", {"path": "."})]),
            LLMResponse(text="child saw a.txt"),
            LLMResponse(text="parent summarized child result"),
        ]
    )
    engine = QueryEngine(tmp_path, llm=llm)

    result = asyncio.run(engine.submit_message("delegate file inspection", max_turns=6))

    assert result.text == "parent summarized child result"
    assert any(event["tool_name"] == "agent.run" for event in result.state.tool_events)
    agent_result = next(message for message in result.state.messages if message.role == "tool" and message.name == "agent.run")
    assert "<subagent-result" in agent_result.content
    assert "child saw a.txt" in agent_result.content
    assert "file.list: ok" in agent_result.content
    assert len(engine.checkpoints.list_sessions()) >= 2


def test_background_subagent_status(tmp_path: Path) -> None:
    async def scenario() -> None:
        llm = ScriptedLLM([LLMResponse(text="background child done")])
        engine = QueryEngine(tmp_path, llm=llm)
        ctx = ToolContext(
            workspace=tmp_path,
            session_id="parent",
            artifact_dir=tmp_path / ".nanobot" / "artifacts" / "parent",
            metadata={"fork_depth": 0, "skill_manager": engine.skills},
        )

        started = await engine.subagents.run(
            {
                "description": "background inspection",
                "prompt": "finish in the background",
                "run_in_background": True,
            },
            ctx,
        )
        task_id = started.data["task_id"]
        status = await engine.subagents.status(task_id)
        for _ in range(5):
            if status.data.get("status") != "running":
                break
            await asyncio.sleep(0)
            status = await engine.subagents.status(task_id)

        assert "background child done" in status.content

    asyncio.run(scenario())
