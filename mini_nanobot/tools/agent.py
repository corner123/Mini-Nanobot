from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mini_nanobot.tools.base import PermissionResult, Tool, ToolContext, ToolResult


@dataclass(slots=True)
class AgentDefinition:
    agent_type: str
    when_to_use: str
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    model: str | None = None
    max_turns: int = 10
    background: bool = False
    isolation: str = "in_process"


class AgentTool(Tool):
    name = "agent.run"
    description = "Run a one-layer child agent for an isolated delegated task."
    input_schema = {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "prompt": {"type": "string"},
            "subagent_type": {"type": "string"},
            "model": {"type": "string"},
            "run_in_background": {"type": "boolean", "default": False},
            "name": {"type": "string"},
            "isolation": {"type": "string", "enum": ["in_process", "worktree", "remote"]},
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tool allowlist for the child agent.",
            },
            "disallowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tool denylist for the child agent.",
            },
            "max_turns": {"type": "integer", "minimum": 1, "default": 8},
        },
        "required": ["description", "prompt"],
        "additionalProperties": False,
    }

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    async def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult(True)

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        depth = int(ctx.metadata.get("fork_depth", 0))
        if depth >= 1:
            return ToolResult("recursive fork is disabled", is_error=True)
        fork_runner = ctx.metadata.get("fork_runner")
        if fork_runner is None:
            return ToolResult(
                "no fork runner configured; AgentTool is available as an integration point",
                data={"prompt": args["prompt"], "description": args["description"]},
            )
        result = await fork_runner(args, ctx)
        if isinstance(result, ToolResult):
            return result
        return ToolResult(str(result), data={"subagent": args.get("subagent_type", "default")})


class AgentStatusTool(Tool):
    name = "agent.status"
    description = "Check an in-process background sub-agent started by agent.run."
    input_schema = {
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
        "additionalProperties": False,
    }

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        runner = ctx.metadata.get("subagent_runner")
        if runner is None:
            return ToolResult("no sub-agent runner configured", is_error=True)
        return await runner.status(args["task_id"])
