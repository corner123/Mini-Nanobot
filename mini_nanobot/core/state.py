from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Literal
from uuid import uuid4


Role = Literal["system", "user", "assistant", "tool"]
StepStatus = Literal["pending", "in_progress", "completed", "blocked"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class Message:
    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    is_meta: bool = False
    created_at: str = field(default_factory=utc_now)

    def short(self, limit: int = 240) -> str:
        text = self.content.replace("\n", " ")
        return text if len(text) <= limit else text[:limit] + "..."


@dataclass(slots=True)
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str = field(default_factory=lambda: f"tool_{uuid4().hex[:12]}")


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens += other.total_tokens
        self.cost_usd += other.cost_usd


@dataclass(slots=True)
class PlanStep:
    text: str
    status: StepStatus = "pending"
    id: str = field(default_factory=lambda: f"step_{uuid4().hex[:8]}")


@dataclass(slots=True)
class AgentState:
    task: str
    session_id: str = field(default_factory=lambda: uuid4().hex)
    messages: list[Message] = field(default_factory=list)
    context_projection: list[Message] = field(default_factory=list)
    plan: list[PlanStep] = field(default_factory=list)
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    turns: int = 0
    task_budget_remaining: int | None = None
    compacted_summaries: list[str] = field(default_factory=list)
    recent_files: list[str] = field(default_factory=list)
    invoked_skills: list[str] = field(default_factory=list)
    query_source: str = "user"
    metadata: dict[str, Any] = field(default_factory=dict)
    completed: bool = False
    final_response: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def add_message(self, message: Message) -> None:
        self.messages.append(message)
        if self.context_projection:
            self.context_projection.append(message)
        self.updated_at = utc_now()

    def active_messages(self) -> list[Message]:
        return self.context_projection or self.messages

    def set_context_projection(self, messages: list[Message]) -> None:
        self.context_projection = messages
        self.updated_at = utc_now()

    def add_tool_event(self, event: dict[str, Any]) -> None:
        event.setdefault("created_at", utc_now())
        self.tool_events.append(event)
        self.updated_at = utc_now()

    def remember_file(self, path: str, max_items: int = 10) -> None:
        normalized = path.replace("\\", "/")
        if normalized in self.recent_files:
            self.recent_files.remove(normalized)
        self.recent_files.insert(0, normalized)
        del self.recent_files[max_items:]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["metadata"] = _json_safe_dict(self.metadata)
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentState":
        messages = [Message(**m) for m in data.get("messages", [])]
        context_projection = [Message(**m) for m in data.get("context_projection", [])]
        plan = [PlanStep(**s) for s in data.get("plan", [])]
        usage_data = data.get("usage", {})
        usage = Usage(**usage_data)
        return cls(
            task=data["task"],
            session_id=data.get("session_id") or uuid4().hex,
            messages=messages,
            context_projection=context_projection,
            plan=plan,
            tool_events=data.get("tool_events", []),
            usage=usage,
            turns=data.get("turns", 0),
            task_budget_remaining=data.get("task_budget_remaining"),
            compacted_summaries=data.get("compacted_summaries", []),
            recent_files=data.get("recent_files", []),
            invoked_skills=data.get("invoked_skills", []),
            query_source=data.get("query_source", "user"),
            metadata=data.get("metadata", {}),
            completed=data.get("completed", False),
            final_response=data.get("final_response"),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
        )

    @classmethod
    def from_json(cls, payload: str) -> "AgentState":
        return cls.from_dict(json.loads(payload))


@dataclass(slots=True)
class QueryEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _json_safe_dict(value: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, item in value.items():
        try:
            json.dumps(item)
        except TypeError:
            continue
        safe[key] = item
    return safe
