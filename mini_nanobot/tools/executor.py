from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mini_nanobot.core.state import ToolCall, utc_now
from mini_nanobot.hooks.manager import HookManager, POST_TOOL_USE, PRE_TOOL_USE
from mini_nanobot.tools.base import ToolContext, ToolResult
from mini_nanobot.tools.registry import ToolRegistry


class StreamingToolExecutor:
    """Executes tool calls with read-only parallelism and write serialization."""

    def __init__(
        self,
        registry: ToolRegistry,
        hooks: HookManager | None = None,
        max_concurrency: int = 4,
    ) -> None:
        self.registry = registry
        self.hooks = hooks or HookManager()
        self.max_concurrency = max_concurrency

    async def execute_many(self, calls: list[ToolCall], ctx: ToolContext) -> list[tuple[ToolCall, ToolResult]]:
        results: list[tuple[ToolCall, ToolResult]] = []
        batch: list[ToolCall] = []

        async def flush_batch() -> None:
            nonlocal batch
            if not batch:
                return
            sem = asyncio.Semaphore(self.max_concurrency)

            async def guarded(call: ToolCall) -> tuple[ToolCall, ToolResult]:
                async with sem:
                    return await self.execute_one(call, ctx)

            results.extend(await asyncio.gather(*(guarded(call) for call in batch)))
            batch = []

        for call in calls:
            try:
                tool = self.registry.get(call.name)
                concurrent = tool.is_concurrency_safe(call.args)
            except Exception:
                concurrent = False
            if concurrent:
                batch.append(call)
                continue
            await flush_batch()
            results.append(await self.execute_one(call, ctx))
        await flush_batch()
        return results

    async def execute_one(self, call: ToolCall, ctx: ToolContext) -> tuple[ToolCall, ToolResult]:
        try:
            tool = self.registry.get(call.name)
            await tool.validate_input(call.args, ctx)
            hook_decision = await self.hooks.check(
                PRE_TOOL_USE,
                {"tool_name": tool.name, "tool_call_id": call.id, "args": call.args, "ctx": ctx},
            )
            if not hook_decision.allow:
                return call, ToolResult(hook_decision.reason or "blocked by hook", is_error=True)
            if hook_decision.args is not None:
                call.args = hook_decision.args
            permission = await tool.check_permissions(call.args, ctx)
            if not permission.allowed:
                return call, ToolResult(permission.reason, is_error=True)
            result = await tool.run(call.args, ctx)
            result = self._persist_if_large(tool.name, call.id, result, ctx.artifact_dir, tool.max_result_size_chars)
        except Exception as exc:
            result = ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)
        await self.hooks.emit(
            POST_TOOL_USE,
            {
                "tool_name": call.name,
                "tool_call_id": call.id,
                "args": call.args,
                "result": result,
                "ctx": ctx,
            },
        )
        return call, result

    def _persist_if_large(
        self,
        tool_name: str,
        call_id: str,
        result: ToolResult,
        artifact_dir: Path,
        limit: int,
    ) -> ToolResult:
        if result.is_error or len(result.content) <= limit:
            return result
        out_dir = artifact_dir / "tool-results"
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact = out_dir / f"{utc_now().replace(':', '-')}_{tool_name.replace('.', '_')}_{call_id}.txt"
        artifact.write_text(result.content, encoding="utf-8")
        preview = result.content[: limit // 2] + "\n...[full output stored on disk]...\n" + result.content[-limit // 2 :]
        return ToolResult(
            content=f"{preview}\n\nFull output: {artifact}",
            data=result.data,
            is_error=False,
            artifact_path=str(artifact),
            truncated=True,
            new_messages=result.new_messages,
            context_updates=result.context_updates,
        )
