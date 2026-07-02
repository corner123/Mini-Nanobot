from __future__ import annotations

from pathlib import Path

from mini_nanobot.tools.base import PermissionLevel, PermissionResult, Tool, ToolContext, ToolResult


class FileReadTool(Tool):
    name = "file.read"
    aliases = ("Read",)
    description = "Read a UTF-8 text file inside the workspace."
    max_result_size_chars = 20_000
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "default": 400},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def is_read_only(self, args: dict) -> bool:
        return True

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve_workspace_path(args["path"])
        if not path.exists():
            return ToolResult(f"file not found: {args['path']}", is_error=True)
        if path.is_dir():
            return ToolResult(f"path is a directory: {args['path']}", is_error=True)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        offset = int(args.get("offset", 0))
        limit = int(args.get("limit", 400))
        selected = lines[offset : offset + limit]
        numbered = [f"{offset + i + 1:>5} | {line}" for i, line in enumerate(selected)]
        return ToolResult(
            content="\n".join(numbered),
            data={"path": str(path), "line_count": len(lines), "offset": offset, "limit": limit},
            context_updates={"recent_file": str(path)},
        )


class FileWriteTool(Tool):
    name = "file.write"
    aliases = ("Write",)
    description = "Write a UTF-8 text file inside the workspace."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "append": {"type": "boolean", "default": False},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    def is_read_only(self, args: dict) -> bool:
        return False

    async def check_permissions(self, args: dict, ctx: ToolContext) -> PermissionResult:
        if PermissionLevel.WRITE_WORKSPACE in ctx.permissions:
            return PermissionResult(True, level=PermissionLevel.WRITE_WORKSPACE)
        return PermissionResult(False, "file.write requires write_workspace permission", PermissionLevel.WRITE_WORKSPACE)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve_workspace_path(args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        if args.get("append", False):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(args["content"])
        else:
            path.write_text(args["content"], encoding="utf-8")
        return ToolResult(f"wrote {path}", data={"path": str(path), "bytes": path.stat().st_size})


class FilePatchTool(Tool):
    name = "file.patch"
    aliases = ("Edit",)
    description = "Patch a file by replacing an exact old string with a new string."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old": {"type": "string"},
            "new": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["path", "old", "new"],
        "additionalProperties": False,
    }

    async def check_permissions(self, args: dict, ctx: ToolContext) -> PermissionResult:
        if PermissionLevel.WRITE_WORKSPACE in ctx.permissions:
            return PermissionResult(True, level=PermissionLevel.WRITE_WORKSPACE)
        return PermissionResult(False, "file.patch requires write_workspace permission", PermissionLevel.WRITE_WORKSPACE)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve_workspace_path(args["path"])
        original = path.read_text(encoding="utf-8")
        old = args["old"]
        if old not in original:
            return ToolResult("old string not found; patch not applied", is_error=True)
        count = -1 if args.get("replace_all", False) else 1
        updated = original.replace(old, args["new"], count)
        path.write_text(updated, encoding="utf-8")
        replacements = original.count(old) if count == -1 else 1
        return ToolResult(f"patched {path} ({replacements} replacement(s))", data={"path": str(path)})


class FileListTool(Tool):
    name = "file.list"
    aliases = ("Glob",)
    description = "List files below a workspace path."
    max_result_size_chars = 12_000
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "default": "."},
            "pattern": {"type": "string", "default": "*"},
            "max_results": {"type": "integer", "minimum": 1, "default": 200},
        },
        "additionalProperties": False,
    }

    def is_read_only(self, args: dict) -> bool:
        return True

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        base = ctx.resolve_workspace_path(args.get("path", "."))
        pattern = args.get("pattern", "*")
        max_results = int(args.get("max_results", 200))
        if not base.exists():
            return ToolResult(f"path not found: {args.get('path', '.')}", is_error=True)
        items = []
        iterator = base.rglob(pattern) if base.is_dir() else [base]
        for path in iterator:
            if ".git" in path.parts or ".nanobot" in path.parts:
                continue
            rel = path.relative_to(ctx.workspace)
            items.append(str(rel).replace("\\", "/") + ("/" if path.is_dir() else ""))
            if len(items) >= max_results:
                break
        return ToolResult("\n".join(items), data={"count": len(items)})
