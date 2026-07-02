from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Callable


SYSTEM_PROMPT_DYNAMIC_BOUNDARY = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"


@dataclass(slots=True)
class PromptSection:
    name: str
    content: str
    cached: bool = True
    reason: str | None = None


class SystemPromptBuilder:
    """Builds stable and dynamic prompt sections.

    The builder keeps stable policy text away from volatile workspace/date
    context. That mirrors the cache-friendly layering described in the design
    note while remaining provider-agnostic.
    """

    def __init__(self) -> None:
        self._section_factories: dict[str, Callable[[], str]] = {}
        self._cache: dict[str, str] = {}
        self._uncached: dict[str, tuple[Callable[[], str], str]] = {}

    def section(self, name: str, factory: Callable[[], str]) -> None:
        self._section_factories[name] = factory

    def dangerous_uncached_section(self, name: str, factory: Callable[[], str], reason: str) -> None:
        if not reason:
            raise ValueError("uncached prompt sections must provide a reason")
        self._uncached[name] = (factory, reason)

    def clear_cache(self) -> None:
        self._cache.clear()

    def build(self, workspace: Path, mode: str = "interactive", append: str = "") -> str:
        if not self._section_factories:
            self._install_defaults()
        static_parts = []
        for name, factory in self._section_factories.items():
            if name not in self._cache:
                self._cache[name] = factory()
            static_parts.append(self._cache[name])
        dynamic_parts = [
            f"Workspace hash: {sha1(str(workspace.resolve()).encode()).hexdigest()[:12]}",
            f"Run mode: {mode}",
        ]
        for name, (factory, reason) in self._uncached.items():
            dynamic_parts.append(f"[uncached:{name}; reason={reason}]\n{factory()}")
        if append:
            dynamic_parts.append(append)
        return "\n\n".join(static_parts + [SYSTEM_PROMPT_DYNAMIC_BOUNDARY] + dynamic_parts)

    def _install_defaults(self) -> None:
        self.section(
            "identity",
            lambda: (
                "You are Mini-Nanobot, a lightweight code-task agent. "
                "Follow a ReAct loop: reason, call tools, observe, and stop when the task is done."
            ),
        )
        self.section(
            "tool_policy",
            lambda: (
                "All side effects must go through tools. Treat tool errors as data and recover. "
                "Prefer read-only inspection before writes. Verify memory hints against the current workspace."
            ),
        )
        self.section(
            "safety",
            lambda: (
                "Never execute destructive shell commands unless the permission context explicitly allows it. "
                "Keep file operations inside the workspace."
            ),
        )
