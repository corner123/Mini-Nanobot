from __future__ import annotations

from mini_nanobot.skills.loader import SkillManager
from mini_nanobot.tools.base import Tool, ToolContext, ToolResult


class SkillTool(Tool):
    name = "skill.load"
    description = "Load a skill's full instructions after seeing its metadata."
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }

    def is_read_only(self, args: dict) -> bool:
        return True

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        manager = ctx.metadata.get("skill_manager")
        if manager is None:
            manager = SkillManager([])
        loaded = manager.load(args["name"])
        return ToolResult(loaded.body, data={"name": loaded.metadata.name})
