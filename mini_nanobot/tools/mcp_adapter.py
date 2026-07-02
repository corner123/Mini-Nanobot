from __future__ import annotations

from typing import Any

from mini_nanobot.tools.base import Tool, ToolContext, ToolResult


class MCPToolAdapter(Tool):
    """Wraps an MCP tool as a Mini-Nanobot Tool.

    The adapter keeps MCP optional. If the mcp SDK is not installed, callers can
    still construct the rest of the framework.
    """

    def __init__(self, name: str, description: str, input_schema: dict[str, Any], client: Any) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.client = client

    def is_read_only(self, args: dict) -> bool:
        return False

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        if self.client is None:
            return ToolResult("MCP client is not configured", is_error=True)
        result = await self.client.call_tool(self.name, args)
        text = str(result)
        return ToolResult(text, data=result)
