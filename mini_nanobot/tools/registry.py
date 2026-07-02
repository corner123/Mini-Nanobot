from __future__ import annotations

from collections import OrderedDict
from typing import Iterable

from mini_nanobot.tools.base import Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: OrderedDict[str, Tool] = OrderedDict()
        self._aliases: dict[str, str] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool
        for alias in tool.aliases:
            self._aliases[alias] = tool.name

    def extend(self, tools: Iterable[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Tool:
        canonical = self._aliases.get(name, name)
        try:
            return self._tools[canonical]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def to_model_tools(self) -> list[dict]:
        return [tool.to_model_schema() for tool in self._tools.values()]


def create_default_registry(workspace=None) -> ToolRegistry:
    from mini_nanobot.tools.agent import AgentTool
    from mini_nanobot.tools.filesystem import FileListTool, FilePatchTool, FileReadTool, FileWriteTool
    from mini_nanobot.tools.git import GitDiffTool, GitLogTool, GitShowTool, GitStatusTool
    from mini_nanobot.tools.search import RgSearchTool
    from mini_nanobot.tools.shell import ShellTool
    from mini_nanobot.skills.tool import SkillTool

    registry = ToolRegistry()
    registry.extend(
        [
            FileReadTool(),
            FileWriteTool(),
            FilePatchTool(),
            FileListTool(),
            RgSearchTool(),
            GitStatusTool(),
            GitDiffTool(),
            GitShowTool(),
            GitLogTool(),
            ShellTool(),
            SkillTool(),
            AgentTool(),
        ]
    )
    return registry
