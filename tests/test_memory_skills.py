from __future__ import annotations

from pathlib import Path

from mini_nanobot.memory.long_term import LongTermMemoryStore
from mini_nanobot.skills.loader import SkillManager


def test_long_term_memory_add_and_recall(tmp_path: Path) -> None:
    store = LongTermMemoryStore(tmp_path / ".nanobot" / "memory", tmp_path)
    store.add("feedback", "Terse replies", "prefer concise final answers", "Use short final answers unless asked.")

    records = store.recall("concise answers")

    assert records
    assert records[0].kind == "feedback"
    assert store.index_attachment() is not None


def test_skill_manager_discovers_and_loads(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".nanobot" / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review changed code\nwhen_to_use: Use for code review\n---\n\nReview carefully.",
        encoding="utf-8",
    )
    manager = SkillManager([tmp_path / ".nanobot" / "skills"])

    discovered = manager.discover()
    loaded = manager.load("review")

    assert discovered[0].name == "review"
    assert "Review carefully" in loaded.body
