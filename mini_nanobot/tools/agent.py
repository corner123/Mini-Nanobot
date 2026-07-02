from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mini_nanobot.tools.base import Tool, ToolContext, ToolResult


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
    description = "Fork a non-recursive sub-agent for an isolated task."
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
        },
        "required": ["description", "prompt"],
        "additionalProperties": False,
    }

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
        return ToolResult(str(result), data={"subagent": args.get("subagent_type", "default")})
