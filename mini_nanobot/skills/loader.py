from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(slots=True)
class SkillMetadata:
    name: str
    description: str
    when_to_use: str = ""
    source: str = "project"
    path: Path | None = None


@dataclass(slots=True)
class LoadedSkill:
    metadata: SkillMetadata
    body: str


class SkillManager:
    def __init__(self, skill_dirs: list[Path] | None = None) -> None:
        self.skill_dirs = skill_dirs or []
        self._metadata: dict[str, SkillMetadata] = {}
        self._invoked: dict[str, LoadedSkill] = {}

    def discover(self) -> list[SkillMetadata]:
        for root in self.skill_dirs:
            if not root.exists():
                continue
            for skill_md in root.glob("*/SKILL.md"):
                meta, _ = self._parse_skill(skill_md)
                self._metadata[meta.name] = meta
        return list(self._metadata.values())

    def render_attachment(self, budget_chars: int = 4_000) -> str:
        metadata = self.discover()
        lines = ["<system-reminder>", "The following skills are available:"]
        for item in metadata:
            desc = item.when_to_use or item.description
            line = f"- {item.name}: {desc}"
            if sum(len(x) for x in lines) + len(line) > budget_chars:
                line = f"- {item.name}"
            lines.append(line)
            if sum(len(x) for x in lines) > budget_chars:
                break
        lines.append("</system-reminder>")
        return "\n".join(lines)

    def load(self, name: str) -> LoadedSkill:
        if not self._metadata:
            self.discover()
        try:
            meta = self._metadata[name]
        except KeyError as exc:
            raise KeyError(f"unknown skill: {name}") from exc
        _, body = self._parse_skill(meta.path)
        loaded = LoadedSkill(meta, body)
        self._invoked[name] = loaded
        return loaded

    def invoked_attachment(self, budget_chars: int = 10_000) -> str:
        parts = []
        for loaded in reversed(list(self._invoked.values())):
            body = loaded.body[: min(len(loaded.body), 3_000)]
            parts.append(f"<skill name=\"{loaded.metadata.name}\">\n{body}\n</skill>")
            if sum(len(p) for p in parts) > budget_chars:
                parts.pop()
                break
        return "\n\n".join(parts)

    def _parse_skill(self, path: Path | None) -> tuple[SkillMetadata, str]:
        if path is None:
            raise ValueError("skill path is missing")
        text = path.read_text(encoding="utf-8")
        frontmatter: dict[str, str] = {}
        body = text
        match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
        if match:
            body = match.group(2)
            for line in match.group(1).splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    frontmatter[key.strip()] = value.strip().strip("\"'")
        name = frontmatter.get("name") or path.parent.name
        meta = SkillMetadata(
            name=name,
            description=frontmatter.get("description", ""),
            when_to_use=frontmatter.get("when_to_use", ""),
            source=frontmatter.get("source", "project"),
            path=path,
        )
        return meta, body
