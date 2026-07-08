from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from uuid import uuid4

from mini_nanobot.context.compressor import ContextBudget, ContextCompressor
from mini_nanobot.context.tokenizer import TokenCounter
from mini_nanobot.core.prompts import SystemPromptBuilder
from mini_nanobot.core.query import query
from mini_nanobot.core.state import AgentState, Message
from mini_nanobot.hooks.manager import HookManager
from mini_nanobot.llm.base import LLMProvider
from mini_nanobot.memory.checkpoint import SQLiteCheckpointStore
from mini_nanobot.tools.base import PermissionLevel, ToolContext, ToolResult
from mini_nanobot.tools.registry import ToolRegistry


READ_ONLY_TOOLS = {
    "file.read",
    "file.list",
    "search.rg",
    "git.status",
    "git.diff",
    "git.show",
    "git.log",
    "skill.load",
}

PROFILE_TOOLS = {
    "default": READ_ONLY_TOOLS,
    "researcher": READ_ONLY_TOOLS,
    "reviewer": READ_ONLY_TOOLS,
    "tester": READ_ONLY_TOOLS | {"shell.run"},
    "coder": READ_ONLY_TOOLS | {"file.write", "file.patch", "shell.run"},
}

WRITE_TOOLS = {"file.write", "file.patch"}
EXECUTE_TOOLS = {"shell.run"}
AGENT_TOOLS = {"agent.run", "agent.status"}


@dataclass(slots=True)
class SubAgentResult:
    subagent_id: str
    session_id: str
    subagent_type: str
    name: str
    status: str
    final_response: str
    allowed_tools: list[str]
    permissions: list[str]
    tool_events: list[dict]
    recent_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_tool_content(self) -> str:
        events = "\n".join(
            f"- {event.get('tool_name')}: {'error' if event.get('is_error') else 'ok'}"
            for event in self.tool_events[-12:]
        )
        if not events:
            events = "- none"
        files = "\n".join(f"- {path}" for path in self.recent_files[:8]) or "- none"
        notes = "\n".join(f"- {note}" for note in self.notes) or "- none"
        return (
            f"<subagent-result id=\"{self.subagent_id}\" type=\"{self.subagent_type}\" status=\"{self.status}\">\n"
            f"Name: {self.name}\n"
            f"Child session: {self.session_id}\n"
            f"Allowed tools: {', '.join(self.allowed_tools) or 'none'}\n"
            f"Permissions: {', '.join(self.permissions) or 'none'}\n\n"
            "Summary:\n"
            f"{self.final_response.strip() or '(no final response)'}\n\n"
            "Recent files:\n"
            f"{files}\n\n"
            "Tool events:\n"
            f"{events}\n\n"
            "Notes:\n"
            f"{notes}\n"
            "</subagent-result>"
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class BackgroundSubAgent:
    task_id: str
    description: str
    subagent_type: str
    task: asyncio.Task[SubAgentResult]


class SubAgentRunner:
    """Runs one-layer child agents as delegated tools.

    The runner intentionally keeps the first production step small: child
    agents run in-process, have their own AgentState and checkpoint, and get a
    reduced tool registry. Worktree and remote execution can be added behind
    the same AgentTool contract later.
    """

    def __init__(
        self,
        *,
        workspace,
        llm: LLMProvider,
        registry: ToolRegistry,
        checkpoint: SQLiteCheckpointStore,
        hooks: HookManager,
        prompt_builder: SystemPromptBuilder | None = None,
        max_context_tokens: int = 32_000,
        max_turns: int = 8,
    ) -> None:
        self.workspace = workspace
        self.llm = llm
        self.registry = registry
        self.checkpoint = checkpoint
        self.hooks = hooks
        self.prompt_builder = prompt_builder or SystemPromptBuilder()
        self.max_context_tokens = max_context_tokens
        self.max_turns = max_turns
        self._background: dict[str, BackgroundSubAgent] = {}

    async def __call__(self, args: dict, ctx: ToolContext) -> ToolResult:
        return await self.run(args, ctx)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        if args.get("run_in_background", False):
            task_id = f"subagent_{uuid4().hex[:12]}"
            task = asyncio.create_task(self._run_child(args, ctx, task_id))
            self._background[task_id] = BackgroundSubAgent(
                task_id=task_id,
                description=args["description"],
                subagent_type=args.get("subagent_type", "default"),
                task=task,
            )
            return ToolResult(
                f"Background sub-agent started: {task_id}",
                data={
                    "task_id": task_id,
                    "description": args["description"],
                    "subagent_type": args.get("subagent_type", "default"),
                    "status": "running",
                },
            )

        result = await self._run_child(args, ctx, f"subagent_{uuid4().hex[:12]}")
        return ToolResult(
            result.to_tool_content(),
            data=result.to_dict(),
            is_error=result.status == "failed",
            context_updates={"recent_file": result.recent_files[0]} if result.recent_files else {},
        )

    async def status(self, task_id: str) -> ToolResult:
        item = self._background.get(task_id)
        if item is None:
            return ToolResult(f"unknown background sub-agent: {task_id}", is_error=True)
        if not item.task.done():
            return ToolResult(
                f"Background sub-agent still running: {task_id}",
                data={
                    "task_id": task_id,
                    "description": item.description,
                    "subagent_type": item.subagent_type,
                    "status": "running",
                },
            )
        try:
            result = item.task.result()
        except Exception as exc:  # pragma: no cover - defensive task boundary
            return ToolResult(f"background sub-agent failed: {type(exc).__name__}: {exc}", is_error=True)
        return ToolResult(
            result.to_tool_content(),
            data={"task_id": task_id, **result.to_dict()},
            is_error=result.status == "failed",
        )

    async def _run_child(self, args: dict, parent_ctx: ToolContext, subagent_id: str) -> SubAgentResult:
        isolation = args.get("isolation") or "in_process"
        subagent_type = args.get("subagent_type") or "default"
        name = args.get("name") or subagent_type
        notes: list[str] = []
        if isolation != "in_process":
            return SubAgentResult(
                subagent_id=subagent_id,
                session_id="",
                subagent_type=subagent_type,
                name=name,
                status="failed",
                final_response=f"isolation mode '{isolation}' is not enabled in this runtime",
                allowed_tools=[],
                permissions=[],
                tool_events=[],
                notes=["Only in_process child agents are implemented."],
            )

        child_permissions = self._child_permissions(subagent_type, parent_ctx.permissions)
        allowed_tools, scope_notes = self._allowed_tools(args, subagent_type, child_permissions)
        notes.extend(scope_notes)
        if args.get("model"):
            notes.append("model override is recorded but this in-process runner uses the parent LLM provider")
        child_registry = self._child_registry(allowed_tools)

        child_state = AgentState(task=args["prompt"], query_source="subagent")
        child_state.metadata["parent_session_id"] = parent_ctx.session_id
        child_state.metadata["subagent_id"] = subagent_id
        child_state.metadata["subagent_type"] = subagent_type
        child_state.metadata["fork_depth"] = int(parent_ctx.metadata.get("fork_depth", 0)) + 1
        if parent_ctx.metadata.get("skill_manager") is not None:
            child_state.metadata["skill_manager"] = parent_ctx.metadata["skill_manager"]

        system_prompt = self.prompt_builder.build(
            self.workspace,
            mode="subagent",
            append=self._subagent_contract(args, subagent_id, allowed_tools, child_permissions),
        )
        child_state.add_message(Message(role="system", content=system_prompt))
        child_state.add_message(
            Message(
                role="user",
                content=self._parent_attachment(args, parent_ctx, subagent_id, allowed_tools, child_permissions),
                is_meta=True,
                name="parent-delegation",
            )
        )
        child_state.add_message(Message(role="user", content=args["prompt"]))

        compressor = ContextCompressor(
            TokenCounter(),
            ContextBudget(max_context_tokens=self.max_context_tokens),
            self.hooks,
        )

        try:
            await query(
                state=child_state,
                llm=self.llm,
                registry=child_registry,
                workspace=self.workspace,
                checkpoint=self.checkpoint,
                compressor=compressor,
                hooks=self.hooks,
                permissions=child_permissions,
                max_turns=int(args.get("max_turns") or self.max_turns),
            )
            status = "completed" if child_state.completed else "stopped"
            final_response = child_state.final_response or ""
        except Exception as exc:  # pragma: no cover - keeps parent agent alive
            status = "failed"
            final_response = f"{type(exc).__name__}: {exc}"

        return SubAgentResult(
            subagent_id=subagent_id,
            session_id=child_state.session_id,
            subagent_type=subagent_type,
            name=name,
            status=status,
            final_response=final_response,
            allowed_tools=sorted(allowed_tools),
            permissions=sorted(level.value for level in child_permissions),
            tool_events=child_state.tool_events,
            recent_files=child_state.recent_files,
            notes=notes,
        )

    def _child_permissions(self, subagent_type: str, parent_permissions: set[PermissionLevel]) -> set[PermissionLevel]:
        permissions = {PermissionLevel.READ_ONLY}
        profile = subagent_type.lower()
        if profile in {"tester", "coder"} and PermissionLevel.EXECUTE_SAFE in parent_permissions:
            permissions.add(PermissionLevel.EXECUTE_SAFE)
        if profile == "coder" and PermissionLevel.WRITE_WORKSPACE in parent_permissions:
            permissions.add(PermissionLevel.WRITE_WORKSPACE)
        return permissions

    def _allowed_tools(
        self,
        args: dict,
        subagent_type: str,
        permissions: set[PermissionLevel],
    ) -> tuple[set[str], list[str]]:
        profile = subagent_type.lower()
        requested = self._tool_set(args.get("allowed_tools")) or set(PROFILE_TOOLS.get(profile, PROFILE_TOOLS["default"]))
        requested -= self._tool_set(args.get("disallowed_tools"))
        requested -= AGENT_TOOLS
        notes: list[str] = []

        if PermissionLevel.WRITE_WORKSPACE not in permissions:
            blocked = sorted(requested & WRITE_TOOLS)
            if blocked:
                notes.append(f"write tools removed because parent did not grant write permission: {', '.join(blocked)}")
            requested -= WRITE_TOOLS
        if PermissionLevel.EXECUTE_SAFE not in permissions:
            blocked = sorted(requested & EXECUTE_TOOLS)
            if blocked:
                notes.append(f"execute tools removed because parent did not grant execute permission: {', '.join(blocked)}")
            requested -= EXECUTE_TOOLS

        available = {tool.name for tool in self.registry.list()}
        unknown = sorted(requested - available)
        if unknown:
            notes.append(f"unknown tools ignored: {', '.join(unknown)}")
        requested &= available
        if not requested:
            requested = set(READ_ONLY_TOOLS & available)
            notes.append("empty tool scope replaced with default read-only tools")
        return requested, notes

    def _tool_set(self, value) -> set[str]:
        if not value:
            return set()
        if isinstance(value, str):
            return {value}
        try:
            return {str(item) for item in value if item}
        except TypeError:
            return {str(value)}

    def _child_registry(self, allowed_tools: set[str]) -> ToolRegistry:
        child = ToolRegistry()
        for tool in self.registry.list():
            if tool.name in allowed_tools:
                child.register(tool)
        return child

    def _subagent_contract(
        self,
        args: dict,
        subagent_id: str,
        allowed_tools: set[str],
        permissions: set[PermissionLevel],
    ) -> str:
        return (
            "Sub-agent execution contract:\n"
            f"- Sub-agent id: {subagent_id}\n"
            f"- Delegated task: {args['description']}\n"
            "- Work only on the delegated task and do not broaden scope.\n"
            "- Do not call other agents; recursive delegation is disabled.\n"
            "- Return concise findings, decisions, files inspected, and remaining risks.\n"
            f"- Allowed tools: {', '.join(sorted(allowed_tools)) or 'none'}.\n"
            f"- Permission levels: {', '.join(sorted(level.value for level in permissions))}."
        )

    def _parent_attachment(
        self,
        args: dict,
        parent_ctx: ToolContext,
        subagent_id: str,
        allowed_tools: set[str],
        permissions: set[PermissionLevel],
    ) -> str:
        return (
            "<system-reminder name=\"parent-delegation\">\n"
            f"Parent session: {parent_ctx.session_id}\n"
            f"Sub-agent id: {subagent_id}\n"
            f"Sub-agent type: {args.get('subagent_type', 'default')}\n"
            f"Description: {args['description']}\n"
            f"Allowed tools: {', '.join(sorted(allowed_tools)) or 'none'}\n"
            f"Permissions: {', '.join(sorted(level.value for level in permissions))}\n\n"
            "Return a compact result for the parent agent. Avoid dumping raw tool output unless it is essential.\n"
            "</system-reminder>"
        )
