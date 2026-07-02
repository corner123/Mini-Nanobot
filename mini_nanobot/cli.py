from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

from mini_nanobot.core.query_engine import QueryEngine
from mini_nanobot.llm.base import RuleBasedLLM
from mini_nanobot.llm.openai_provider import OpenAIProvider
from mini_nanobot.tools.base import PermissionLevel
from mini_nanobot.tools.registry import create_default_registry


def main() -> None:
    parser = argparse.ArgumentParser(prog="nanobot", description="Mini-Nanobot code-task agent")
    parser.add_argument("--workspace", default=".", help="Workspace root")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run a task")
    run.add_argument("task")
    run.add_argument("--provider", choices=["offline", "openai"], default="offline")
    run.add_argument("--model", default="gpt-4.1-mini")
    run.add_argument("--write", action="store_true")
    run.add_argument("--execute", action="store_true")
    run.add_argument("--dangerous", action="store_true")
    run.add_argument("--max-turns", type=int, default=20)

    resume = sub.add_parser("resume", help="Resume a session")
    resume.add_argument("session_id")
    resume.add_argument("--max-turns", type=int, default=20)
    resume.add_argument("--write", action="store_true")
    resume.add_argument("--execute", action="store_true")
    resume.add_argument("--dangerous", action="store_true")

    sub.add_parser("sessions", help="List checkpointed sessions")
    sub.add_parser("tools", help="List registered tools")

    memory_add = sub.add_parser("memory-add", help="Add long-term memory")
    memory_add.add_argument("kind", choices=["user", "feedback", "project", "reference"])
    memory_add.add_argument("title")
    memory_add.add_argument("summary")
    memory_add.add_argument("body")

    memory_search = sub.add_parser("memory-search", help="Search long-term memory")
    memory_search.add_argument("query")

    bench = sub.add_parser("bench", help="Run benchmark tasks")
    bench.add_argument("--file", default="benchmarks/tasks.json")

    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()

    if args.command == "tools":
        registry = create_default_registry(workspace)
        for tool in registry.list():
            print(f"{tool.name}\t{tool.description}")
        return

    engine = _engine(args, workspace)

    if args.command == "sessions":
        for session in engine.checkpoints.list_sessions():
            status = "done" if session.completed else "open"
            print(f"{session.session_id}\t{status}\t{session.updated_at}\t{session.task}")
        return

    if args.command == "memory-add":
        record = engine.memory.add(args.kind, args.title, args.summary, args.body)
        print(record.path)
        return

    if args.command == "memory-search":
        for record in engine.memory.recall(args.query):
            print(f"{record.kind}\t{record.title}\t{record.summary}\t{record.path}")
        return

    if args.command == "bench":
        asyncio.run(_run_bench(engine, Path(args.file)))
        return

    if args.command == "run":
        result = asyncio.run(engine.submit_message(args.task, max_turns=args.max_turns))
        print(result.text)
        print(f"\nsession_id={result.state.session_id}")
        return

    if args.command == "resume":
        result = asyncio.run(engine.resume(args.session_id, max_turns=args.max_turns))
        print(result.text)
        print(f"\nsession_id={result.state.session_id}")
        return


def _engine(args: argparse.Namespace, workspace: Path) -> QueryEngine:
    permissions = {PermissionLevel.READ_ONLY}
    if getattr(args, "write", False):
        permissions.add(PermissionLevel.WRITE_WORKSPACE)
    if getattr(args, "execute", False):
        permissions.add(PermissionLevel.EXECUTE_SAFE)
    if getattr(args, "dangerous", False):
        permissions.add(PermissionLevel.DANGEROUS)
    if getattr(args, "provider", "offline") == "openai":
        llm = OpenAIProvider(getattr(args, "model", "gpt-4.1-mini"))
    else:
        llm = RuleBasedLLM()
    return QueryEngine(workspace=workspace, llm=llm, permissions=permissions)


async def _run_bench(engine: QueryEngine, path: Path) -> None:
    tasks = json.loads(path.read_text(encoding="utf-8"))
    passed = 0
    tool_calls = 0
    tool_success = 0
    started = perf_counter()
    for task in tasks:
        result = await engine.submit_message(task["prompt"], max_turns=task.get("max_turns", 6))
        expected = task.get("expect_contains")
        ok = expected is None or expected in result.text or any(expected in m.content for m in result.state.messages)
        passed += int(ok)
        tool_calls += len(result.state.tool_events)
        tool_success += sum(1 for event in result.state.tool_events if not event.get("is_error"))
        print(f"{'PASS' if ok else 'FAIL'}\t{task['name']}\t{result.state.session_id}")
    elapsed = perf_counter() - started
    completion = passed / max(1, len(tasks))
    tool_rate = tool_success / max(1, tool_calls)
    print(json.dumps({"tasks": len(tasks), "completion_rate": completion, "tool_success_rate": tool_rate, "seconds": elapsed}, indent=2))


if __name__ == "__main__":
    main()
