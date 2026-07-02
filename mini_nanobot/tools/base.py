from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class PermissionLevel(str, Enum):
    READ_ONLY = "read_only"
    WRITE_WORKSPACE = "write_workspace"
    EXECUTE_SAFE = "execute_safe"
    GIT_MUTATE = "git_mutate"
    DANGEROUS = "dangerous"


@dataclass(slots=True)
class PermissionResult:
    allowed: bool
    reason: str = ""
    level: PermissionLevel = PermissionLevel.READ_ONLY


@dataclass(slots=True)
class ToolResult:
    content: str
    data: Any = None
    is_error: bool = False
    artifact_path: str | None = None
    truncated: bool = False
    new_messages: list[Any] = field(default_factory=list)
    context_updates: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolContext:
    workspace: Path
    session_id: str
    artifact_dir: Path
    permissions: set[PermissionLevel] = field(default_factory=lambda: {PermissionLevel.READ_ONLY})
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def resolve_workspace_path(self, user_path: str | Path) -> Path:
        path = Path(user_path)
        if not path.is_absolute():
            path = self.workspace / path
        resolved = path.resolve()
        workspace = self.workspace.resolve()
        try:
            resolved.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(f"path escapes workspace: {user_path}") from exc
        return resolved


class Tool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] = {"type": "object"}
    max_result_size_chars: int = 8_000
    aliases: tuple[str, ...] = ()

    def to_model_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
        }

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_destructive(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return self.is_read_only(args)

    async def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> None:
        required = self.input_schema.get("required", [])
        for key in required:
            if key not in args:
                raise ValueError(f"missing required argument: {key}")
        properties = self.input_schema.get("properties", {})
        for key in args:
            if key not in properties and not self.input_schema.get("additionalProperties", True):
                raise ValueError(f"unknown argument: {key}")

    async def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        if self.is_read_only(args):
            return PermissionResult(True, level=PermissionLevel.READ_ONLY)
        if PermissionLevel.WRITE_WORKSPACE in ctx.permissions:
            return PermissionResult(True, level=PermissionLevel.WRITE_WORKSPACE)
        return PermissionResult(False, "write permission required", PermissionLevel.WRITE_WORKSPACE)

    @abstractmethod
    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise NotImplementedError
