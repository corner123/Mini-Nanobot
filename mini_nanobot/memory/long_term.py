from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import re
from typing import Literal

from mini_nanobot.core.state import Message, utc_now


MemoryKind = Literal["user", "feedback", "project", "reference"]


@dataclass(slots=True)
class MemoryRecord:
    kind: MemoryKind
    title: str
    summary: str
    body: str
    path: Path
    updated_at: str


class LongTermMemoryStore:
    kinds = {"user", "feedback", "project", "reference"}

    def __init__(self, root: Path, workspace: Path) -> None:
        digest = hashlib.sha1(str(workspace.resolve()).encode()).hexdigest()[:16]
        self.root = root / digest / "memory"
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "MEMORY.md"

    def add(self, kind: MemoryKind, title: str, summary: str, body: str) -> MemoryRecord:
        if kind not in self.kinds:
            raise ValueError(f"invalid memory kind: {kind}")
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", title.strip().lower()).strip("_") or kind
        path = self.root / f"{kind}_{slug}.md"
        frontmatter = f"---\nkind: {kind}\ntitle: {title}\nsummary: {summary}\nupdated_at: {utc_now()}\n---\n\n"
        path.write_text(frontmatter + body.strip() + "\n", encoding="utf-8")
        self._rebuild_index()
        return MemoryRecord(kind, title, summary, body, path, utc_now())

    def records(self, limit: int = 200) -> list[MemoryRecord]:
        paths = sorted(self.root.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        records: list[MemoryRecord] = []
        for path in paths:
            if path.name == "MEMORY.md":
                continue
            record = self._read_record(path)
            if record:
                records.append(record)
            if len(records) >= limit:
                break
        return records

    def index_attachment(self, max_lines: int = 200, max_bytes: int = 25_000) -> Message | None:
        self._rebuild_index()
        if not self.index_path.exists():
            return None
        data = self.index_path.read_bytes()[:max_bytes].decode("utf-8", errors="replace")
        lines = data.splitlines()[:max_lines]
        if not lines:
            return None
        return Message(role="user", content="<system-reminder>\n" + "\n".join(lines) + "\n</system-reminder>", is_meta=True, name="memory-index")

    def recall(self, query: str, limit: int = 5) -> list[MemoryRecord]:
        words = set(re.findall(r"[\w-]+", query.lower()))
        scored = []
        for record in self.records():
            haystack = f"{record.title} {record.summary} {record.body}".lower()
            score = sum(1 for word in words if word and word in haystack)
            if score:
                scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in scored[:limit]]

    def recall_attachment(self, query: str) -> Message | None:
        records = self.recall(query)
        if not records:
            return None
        blocks = []
        for record in records:
            freshness = self._freshness_warning(record.updated_at)
            blocks.append(
                f"## {record.title}\n"
                f"kind: {record.kind}\n"
                f"summary: {record.summary}\n"
                f"{freshness}\n"
                f"{record.body[:1200]}"
            )
        content = (
            "<system-reminder name=\"recalled-memory\">\n"
            "Treat recalled memory as a hint. Verify paths/functions against the current workspace before relying on it.\n\n"
            + "\n\n".join(blocks)
            + "\n</system-reminder>"
        )
        return Message(role="user", content=content, is_meta=True, name="recalled-memory")

    def _rebuild_index(self) -> None:
        lines = ["# Memory Index", ""]
        for record in self.records():
            rel = record.path.name
            lines.append(f"- [{record.title}]({rel}) - {record.summary}")
        self.index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _read_record(self, path: Path) -> MemoryRecord | None:
        text = path.read_text(encoding="utf-8", errors="replace")
        match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
        if not match:
            return None
        meta: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
        kind = meta.get("kind", "project")
        if kind not in self.kinds:
            kind = "project"
        return MemoryRecord(
            kind=kind,  # type: ignore[arg-type]
            title=meta.get("title", path.stem),
            summary=meta.get("summary", ""),
            body=match.group(2).strip(),
            path=path,
            updated_at=meta.get("updated_at", utc_now()),
        )

    def _freshness_warning(self, updated_at: str) -> str:
        try:
            dt = datetime.fromisoformat(updated_at)
        except ValueError:
            return "warning: memory freshness unknown"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - dt
        if age.days >= 1:
            return f"warning: memory is {age.days} day(s) old; verify before use"
        return "freshness: recent"
