from __future__ import annotations

from mini_nanobot.sandbox.executor import ShellSandboxExecutor
from mini_nanobot.sandbox.policy import CommandSafetyPolicy
from mini_nanobot.tools.base import PermissionLevel, PermissionResult, Tool, ToolContext, ToolResult


class ShellTool(Tool):
    name = "shell.run"
    aliases = ("Bash",)
    description = "Run a shell command in the workspace with safety checks and timeout."
    max_result_size_chars = 24_000
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout_seconds": {"type": "integer", "minimum": 1, "default": 30},
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.policy = CommandSafetyPolicy()
        self.executor = ShellSandboxExecutor(self.policy)

    def is_read_only(self, args: dict) -> bool:
        return self.policy.is_read_only_command(args.get("command", ""))

    def is_destructive(self, args: dict) -> bool:
        return self.policy.inspect(args.get("command", "")).destructive

    def is_concurrency_safe(self, args: dict) -> bool:
        return self.is_read_only(args)

    async def check_permissions(self, args: dict, ctx: ToolContext) -> PermissionResult:
        verdict = self.policy.inspect(args.get("command", ""))
        if not verdict.allowed:
            return PermissionResult(False, verdict.reason, PermissionLevel.DANGEROUS)
        if self.is_read_only(args):
            return PermissionResult(True, level=PermissionLevel.READ_ONLY)
        if verdict.destructive and PermissionLevel.DANGEROUS not in ctx.permissions:
            return PermissionResult(False, "destructive shell command requires dangerous permission", PermissionLevel.DANGEROUS)
        if PermissionLevel.EXECUTE_SAFE in ctx.permissions:
            return PermissionResult(True, level=PermissionLevel.EXECUTE_SAFE)
        return PermissionResult(False, "shell.run requires execute_safe permission", PermissionLevel.EXECUTE_SAFE)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        code, stdout, stderr, verdict = await self.executor.run(
            args["command"],
            cwd=ctx.workspace,
            timeout_seconds=int(args.get("timeout_seconds", 30)),
            env=ctx.env,
        )
        content = []
        if stdout:
            content.append(stdout.rstrip())
        if stderr:
            content.append("[stderr]\n" + stderr.rstrip())
        if not content:
            content.append(f"exit code {code}")
        return ToolResult(
            "\n".join(content),
            data={"exit_code": code, "destructive": verdict.destructive},
            is_error=code != 0,
        )
