from __future__ import annotations

import json
from typing import Any

from mini_nanobot.core.state import AgentState, Message, ToolCall, Usage
from mini_nanobot.llm.base import LLMProvider, LLMResponse


class OpenAIProvider(LLMProvider):
    """Optional OpenAI-compatible provider.

    The framework does not require this dependency for tests. Install the
    ``openai`` extra and set OPENAI_API_KEY to use it.
    """

    name = "openai"

    def __init__(self, model: str = "gpt-4.1-mini"):
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("Install mini-nanobot[openai] to use OpenAIProvider.") from exc
        self.client = AsyncOpenAI()
        self.model = model

    async def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        state: AgentState,
    ) -> LLMResponse:
        api_messages = [
            {
                "role": m.role if m.role != "tool" else "user",
                "content": m.content,
            }
            for m in messages
            if m.role in {"system", "user", "assistant", "tool"}
        ]
        response = await self.client.responses.create(
            model=self.model,
            input=api_messages,
            tools=tools,
        )
        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            item_type = getattr(item, "type", "")
            if item_type in {"function_call", "tool_call"}:
                args = getattr(item, "arguments", "{}") or "{}"
                tool_calls.append(
                    ToolCall(
                        id=getattr(item, "call_id", None) or getattr(item, "id", None),
                        name=getattr(item, "name"),
                        args=json.loads(args) if isinstance(args, str) else args,
                    )
                )
            elif item_type == "message":
                for content in getattr(item, "content", []) or []:
                    if getattr(content, "type", "") in {"output_text", "text"}:
                        text_parts.append(getattr(content, "text", ""))
        usage_data = getattr(response, "usage", None)
        usage = Usage(
            input_tokens=getattr(usage_data, "input_tokens", 0) if usage_data else 0,
            output_tokens=getattr(usage_data, "output_tokens", 0) if usage_data else 0,
            total_tokens=getattr(usage_data, "total_tokens", 0) if usage_data else 0,
        )
        return LLMResponse(text="\n".join(text_parts).strip(), tool_calls=tool_calls, usage=usage, raw=response)
