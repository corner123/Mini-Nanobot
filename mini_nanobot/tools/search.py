from __future__ import annotations

import asyncio
import shutil

from mini_nanobot.tools.base import Tool, ToolContext, ToolResult


class RgSearchTool(Tool):
    name = "search.rg"
    aliases = ("Grep",)
    description = "Search workspace text with ripgrep."
    max_result_size_chars = 20_000
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "default": "."},
            "glob": {"type": "string"},
            "max_matches": {"type": "integer", "minimum": 1, "default": 200},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def is_read_only(self, args: dict) -> bool:
        return True

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        rg = shutil.which("rg")
        if not rg:
            return ToolResult("ripgrep (rg) not found", is_error=True)
        path = ctx.resolve_workspace_path(args.get("path", "."))
        command = [rg, "--line-number", "--no-heading", "--color", "never", args["pattern"], str(path)]
        if args.get("glob"):
            command[1:1] = ["--glob", args["glob"]]
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(ctx.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")
        if proc.returncode not in (0, 1):
            return ToolResult(stderr or stdout, is_error=True)
        lines = stdout.splitlines()[: int(args.get("max_matches", 200))]
        return ToolResult("\n".join(lines), data={"matches": len(lines), "truncated": len(stdout.splitlines()) > len(lines)})
