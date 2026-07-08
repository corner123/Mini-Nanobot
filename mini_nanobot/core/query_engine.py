from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mini_nanobot.context.compressor import ContextBudget, ContextCompressor
from mini_nanobot.context.tokenizer import TokenCounter
from mini_nanobot.core.prompts import SystemPromptBuilder
from mini_nanobot.core.query import query
from mini_nanobot.core.state import AgentState, Message, QueryEvent
from mini_nanobot.core.subagent import SubAgentRunner
from mini_nanobot.hooks.manager import HookManager, SESSION_END, SESSION_START
from mini_nanobot.llm.base import LLMProvider, RuleBasedLLM
from mini_nanobot.memory.checkpoint import SQLiteCheckpointStore
from mini_nanobot.memory.long_term import LongTermMemoryStore
from mini_nanobot.skills.loader import SkillManager
from mini_nanobot.tools.base import PermissionLevel
from mini_nanobot.tools.registry import ToolRegistry, create_default_registry


@dataclass(slots=True)
class QueryResult:
    state: AgentState
    events: list[QueryEvent]

    @property
    def text(self) -> str:
        return self.state.final_response or ""


class QueryEngine:
    """Outer lifecycle manager for conversations."""

    def __init__(
        self,
        workspace: str | Path,
        llm: LLMProvider | None = None,
        registry: ToolRegistry | None = None,
        hooks: HookManager | None = None,
        permissions: set[PermissionLevel] | None = None,
        max_context_tokens: int = 32_000,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.nanobot_dir = self.workspace / ".nanobot"
        self.nanobot_dir.mkdir(exist_ok=True)
        self.llm = llm or RuleBasedLLM()
        self.registry = registry or create_default_registry(self.workspace)
        self.hooks = hooks or HookManager()
        self.permissions = permissions or {PermissionLevel.READ_ONLY}
        self.prompt_builder = SystemPromptBuilder()
        self.checkpoints = SQLiteCheckpointStore(self.nanobot_dir / "checkpoints.sqlite3")
        self.memory = LongTermMemoryStore(self.nanobot_dir / "memory", self.workspace)
        self.skills = SkillManager([self.workspace / ".nanobot" / "skills", self.workspace / ".claude" / "skills"])
        self.compressor = ContextCompressor(
            TokenCounter(),
            ContextBudget(max_context_tokens=max_context_tokens),
            self.hooks,
        )
        self.subagents = SubAgentRunner(
            workspace=self.workspace,
            llm=self.llm,
            registry=self.registry,
            checkpoint=self.checkpoints,
            hooks=self.hooks,
            prompt_builder=self.prompt_builder,
            max_context_tokens=max_context_tokens,
        )

    async def submit_message(self, task: str, session_id: str | None = None, max_turns: int = 20) -> QueryResult:
        if session_id:
            state = self.checkpoints.load(session_id)
            if state is None:
                raise KeyError(f"session not found: {session_id}")
            state.completed = False
        else:
            state = AgentState(task=task)
            system_prompt = self.prompt_builder.build(self.workspace)
            state.add_message(Message(role="system", content=system_prompt))

        await self.hooks.emit(SESSION_START, {"state": state, "workspace": self.workspace})
        self._inject_dynamic_context(state, task)
        state.add_message(Message(role="user", content=task))
        state.metadata["skill_manager"] = self.skills
        state.metadata["fork_depth"] = int(state.metadata.get("fork_depth", 0))
        state.metadata["fork_runner"] = self.subagents.run
        state.metadata["subagent_runner"] = self.subagents

        events = await query(
            state=state,
            llm=self.llm,
            registry=self.registry,
            workspace=self.workspace,
            checkpoint=self.checkpoints,
            compressor=self.compressor,
            hooks=self.hooks,
            permissions=self.permissions,
            max_turns=max_turns,
        )
        await self.hooks.emit(SESSION_END, {"state": state, "workspace": self.workspace})
        return QueryResult(state, events)

    async def resume(self, session_id: str, max_turns: int = 20) -> QueryResult:
        state = self.checkpoints.load(session_id)
        if state is None:
            raise KeyError(f"session not found: {session_id}")
        state.completed = False
        return await self.submit_message("Continue from the latest checkpoint.", session_id=session_id, max_turns=max_turns)

    def _inject_dynamic_context(self, state: AgentState, task: str) -> None:
        index = self.memory.index_attachment()
        if index:
            state.add_message(index)
        recalled = self.memory.recall_attachment(task)
        if recalled:
            state.add_message(recalled)
        skill_menu = self.skills.render_attachment()
        if "The following skills are available:" in skill_menu:
            state.add_message(Message(role="user", content=skill_menu, is_meta=True, name="skills-menu"))
