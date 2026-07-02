from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mini_nanobot.sandbox.policy import CommandSafetyPolicy, CommandVerdict


class ShellSandboxExecutor:
    def __init__(self, policy: CommandSafetyPolicy | None = None, max_output_chars: int = 64_000) -> None:
        self.policy = policy or CommandSafetyPolicy()
        self.max_output_chars = max_output_chars

    async def run(
        self,
        command: str,
        cwd: Path,
        timeout_seconds: int = 30,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str, CommandVerdict]:
        verdict = self.policy.inspect(command)
        if not verdict.allowed:
            return 126, "", verdict.reason, verdict

        safe_env = self._safe_env(env)
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return 124, "", f"command timed out after {timeout_seconds}s", verdict

        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")
        return process.returncode or 0, self._cap(stdout), self._cap(stderr), verdict

    def _safe_env(self, env: dict[str, str] | None) -> dict[str, str]:
        source = dict(os.environ)
        if env:
            source.update(env)
        denied = ("TOKEN", "SECRET", "PASSWORD", "KEY")
        return {k: v for k, v in source.items() if not any(marker in k.upper() for marker in denied)}

    def _cap(self, text: str) -> str:
        if len(text) <= self.max_output_chars:
            return text
        head = text[: self.max_output_chars // 2]
        tail = text[-self.max_output_chars // 2 :]
        return f"{head}\n...[output capped by sandbox]...\n{tail}"
