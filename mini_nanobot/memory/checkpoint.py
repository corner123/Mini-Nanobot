from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from mini_nanobot.core.state import AgentState, utc_now


@dataclass(slots=True)
class SessionInfo:
    session_id: str
    task: str
    completed: bool
    updated_at: str


class SQLiteCheckpointStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    session_id TEXT PRIMARY KEY,
                    task TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    completed INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def save(self, state: AgentState) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO checkpoints(session_id, task, state_json, completed, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    task=excluded.task,
                    state_json=excluded.state_json,
                    completed=excluded.completed,
                    updated_at=excluded.updated_at
                """,
                (state.session_id, state.task, state.to_json(), int(state.completed), state.created_at, utc_now()),
            )

    def load(self, session_id: str) -> AgentState | None:
        with self._connect() as conn:
            row = conn.execute("SELECT state_json FROM checkpoints WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            return None
        return AgentState.from_json(row[0])

    def list_sessions(self, limit: int = 20) -> list[SessionInfo]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT session_id, task, completed, updated_at
                FROM checkpoints
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [SessionInfo(row[0], row[1], bool(row[2]), row[3]) for row in rows]

    def append_event(self, session_id: str, event_type: str, payload_json: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events(session_id, event_type, payload_json, created_at) VALUES(?, ?, ?, ?)",
                (session_id, event_type, payload_json, utc_now()),
            )
