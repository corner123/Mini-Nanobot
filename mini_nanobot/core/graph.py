from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class GraphSpec:
    nodes: list[str]
    edges: list[tuple[str, str]]
    provider: str = "fallback"


def build_query_graph() -> Any:
    """Return a LangGraph graph when available, otherwise a serializable spec."""

    try:
        from langgraph.graph import END, StateGraph
    except Exception:
        return GraphSpec(
            nodes=["prepare_context", "llm_decision", "execute_tools", "checkpoint", "finish"],
            edges=[
                ("prepare_context", "llm_decision"),
                ("llm_decision", "execute_tools"),
                ("execute_tools", "checkpoint"),
                ("checkpoint", "llm_decision"),
                ("llm_decision", "finish"),
            ],
        )

    graph = StateGraph(dict)
    graph.add_node("prepare_context", lambda state: state)
    graph.add_node("llm_decision", lambda state: state)
    graph.add_node("execute_tools", lambda state: state)
    graph.add_node("checkpoint", lambda state: state)
    graph.set_entry_point("prepare_context")
    graph.add_edge("prepare_context", "llm_decision")
    graph.add_edge("llm_decision", "execute_tools")
    graph.add_edge("execute_tools", "checkpoint")
    graph.add_edge("checkpoint", END)
    return graph.compile()
