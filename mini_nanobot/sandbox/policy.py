from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(slots=True)
class CommandVerdict:
    allowed: bool
    reason: str = ""
    destructive: bool = False
    needs_sandbox: bool = True


class CommandSafetyPolicy:
    """Local shell safety filter.

    This is not a substitute for OS isolation, but it blocks obvious hazards
    before a command reaches the shell.
    """

    blocked_patterns = [
        r"\brm\s+-rf\s+[/\\]",
        r"\bdel\s+/[fsq]\s+[/\\]?",
        r"\bformat\b",
        r"\bdiskpart\b",
        r"\bmkfs\b",
        r"\bdd\s+.*\bof=",
        r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;",
        r"\bsudo\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r">\s*/dev/sd[a-z]",
    ]
    destructive_patterns = [
        r"\brm\b",
        r"\bdel\b",
        r"\bRemove-Item\b",
        r"\bgit\s+reset\b",
        r"\bgit\s+checkout\b",
        r"\bgit\s+clean\b",
        r"\bchmod\b",
        r"\bchown\b",
    ]
    read_only_prefixes = (
        "ls",
        "dir",
        "pwd",
        "cat",
        "type",
        "rg",
        "grep",
        "findstr",
        "git status",
        "git diff",
        "git log",
        "git show",
        "python --version",
        "python -V",
    )

    def inspect(self, command: str) -> CommandVerdict:
        normalized = command.strip()
        for pattern in self.blocked_patterns:
            if re.search(pattern, normalized, flags=re.IGNORECASE):
                return CommandVerdict(False, f"blocked dangerous shell pattern: {pattern}", True)
        destructive = any(re.search(p, normalized, flags=re.IGNORECASE) for p in self.destructive_patterns)
        return CommandVerdict(True, destructive=destructive, needs_sandbox=True)

    def is_read_only_command(self, command: str) -> bool:
        normalized = command.strip().lower()
        return any(normalized.startswith(prefix.lower()) for prefix in self.read_only_prefixes)
