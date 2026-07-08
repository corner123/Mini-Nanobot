from __future__ import annotations

import asyncio
from pathlib import Path

from mini_nanobot.core.state import ToolCall
from mini_nanobot.tools.base import PermissionLevel, ToolContext
from mini_nanobot.tools.executor import StreamingToolExecutor
from mini_nanobot.tools.registry import create_default_registry


def run(coro):
    return asyncio.run(coro)


def test_file_write_requires_permission(tmp_path: Path) -> None:
    registry = create_default_registry(tmp_path)
    ctx = ToolContext(workspace=tmp_path, session_id="s", artifact_dir=tmp_path / ".nanobot")
    executor = StreamingToolExecutor(registry)

    _, result = run(executor.execute_many([ToolCall("file.write", {"path": "a.txt", "content": "x"})], ctx))[0]

    assert result.is_error
    assert "write_workspace" in result.content


def test_file_write_and_read(tmp_path: Path) -> None:
    registry = create_default_registry(tmp_path)
    ctx = ToolContext(
        workspace=tmp_path,
        session_id="s",
        artifact_dir=tmp_path / ".nanobot",
        permissions={PermissionLevel.READ_ONLY, PermissionLevel.WRITE_WORKSPACE},
    )
    executor = StreamingToolExecutor(registry)

    run(executor.execute_many([ToolCall("file.write", {"path": "a.txt", "content": "hello\nworld\n"})], ctx))
    _, result = run(executor.execute_many([ToolCall("file.read", {"path": "a.txt"})], ctx))[0]

    assert not result.is_error
    assert "hello" in result.content


def test_shell_blocks_dangerous_command(tmp_path: Path) -> None:
    registry = create_default_registry(tmp_path)
    ctx = ToolContext(
        workspace=tmp_path,
        session_id="s",
        artifact_dir=tmp_path / ".nanobot",
        permissions={PermissionLevel.READ_ONLY, PermissionLevel.EXECUTE_SAFE},
    )
    executor = StreamingToolExecutor(registry)

    _, result = run(executor.execute_many([ToolCall("shell.run", {"command": "rm -rf /"})], ctx))[0]

    assert result.is_error
    assert "dangerous" in result.content.lower() or "blocked" in result.content.lower()


def test_agent_run_blocks_recursive_fork(tmp_path: Path) -> None:
    registry = create_default_registry(tmp_path)
    ctx = ToolContext(
        workspace=tmp_path,
        session_id="s",
        artifact_dir=tmp_path / ".nanobot",
        metadata={"fork_depth": 1},
    )
    executor = StreamingToolExecutor(registry)

    _, result = run(
        executor.execute_many(
            [ToolCall("agent.run", {"description": "nested", "prompt": "try nested delegation"})],
            ctx,
        )
    )[0]

    assert result.is_error
    assert "recursive" in result.content
