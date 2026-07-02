from __future__ import annotations

import asyncio
import shutil

from mini_nanobot.tools.base import Tool, ToolContext, ToolResult


class _GitTool(Tool):
    max_result_size_chars = 20_000

    def is_read_only(self, args: dict) -> bool:
        return True

    async def _git(self, ctx: ToolContext, *args: str) -> ToolResult:
        git = shutil.which("git")
        if not git:
            return ToolResult("git not found", is_error=True)
        proc = await asyncio.create_subprocess_exec(
            git,
            *args,
            cwd=str(ctx.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")
        if proc.returncode != 0:
            return ToolResult(stderr or stdout, is_error=True)
        return ToolResult(stdout.strip(), data={"args": args})


class GitStatusTool(_GitTool):
    name = "git.status"
    description = "Show git working tree status."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        return await self._git(ctx, "status", "--short")


class GitDiffTool(_GitTool):
    name = "git.diff"
    description = "Show git diff."
    input_schema = {
        "type": "object",
        "properties": {"staged": {"type": "boolean", "default": False}, "path": {"type": "string"}},
        "additionalProperties": False,
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        command = ["diff"]
        if args.get("staged"):
            command.append("--staged")
        if args.get("path"):
            command.extend(["--", args["path"]])
        return await self._git(ctx, *command)


class GitShowTool(_GitTool):
    name = "git.show"
    description = "Show a git object."
    input_schema = {
        "type": "object",
        "properties": {"rev": {"type": "string", "default": "HEAD"}},
        "additionalProperties": False,
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        return await self._git(ctx, "show", "--stat", "--oneline", args.get("rev", "HEAD"))


class GitLogTool(_GitTool):
    name = "git.log"
    description = "Show recent commits."
    input_schema = {
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "default": 10}},
        "additionalProperties": False,
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        return await self._git(ctx, "log", "--oneline", f"-{int(args.get('limit', 10))}")
