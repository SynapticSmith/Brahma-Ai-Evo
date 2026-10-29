"""Durable agent sessions: messages, events, and resume.

``workspace_store.py`` already persists UI transcripts, but nothing ever read
them back into a prompt, so a restart was a hard reset. This module owns the
*agent* view of a session — the exact OpenAI-shaped message list the loop
sent, the event stream, and the compaction summary — in the same SQLite file.

Tables are additive and namespaced (``agent_*``), so an older build opening
the database sees only tables it ignores.

The message column is JSON rather than columns because the payload *is* the
protocol: ``tool_calls``, ``tool_call_id`` and ``name`` must round-trip
byte-for-byte or a resumed session fails on the first follow-up request.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class SessionNotFound(LookupError):
    pass


def _now_ms() -> int:
    return int(time.time() * 1000)


def _default_db_path() -> Path:
    from workspace_store import STORE_FILE  # local import: keeps headless CLI fast

    return Path(STORE_FILE)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    cwd         TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    summary     TEXT NOT NULL DEFAULT '',
    turn_count  INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    role        TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_session
    ON agent_messages(session_id, seq);

CREATE TABLE IF NOT EXISTS agent_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_events_session
    ON agent_events(session_id, seq);
"""


@dataclass
class SessionRecord:
    id: str
    title: str
    cwd: str
    model: str
    status: str
    turn_count: int
    created_at: int
    updated_at: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "cwd": self.cwd,
            "model": self.model,
            "status": self.status,
            "turn_count": self.turn_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class AgentSession:
    """One resumable agent conversation."""

    def __init__(
        self,
        session_id: str,
        *,
        cwd: str = "",
        title: str = "",
        model: str = "",
        db_path: Optional[Path] = None,
    ):
        self.id = session_id
        self.cwd = cwd
        self.title = title
        self.model = model
        self._db_path = Path(db_path) if db_path else _default_db_path()
        self._lock = threading.RLock()
        self._ensure_db()
        self._ensure_row()

    # -- storage -----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _ensure_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _ensure_row(self) -> None:
        stamp = _now_ms()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_sessions(id, title, cwd, model, status, summary,
                                           turn_count, created_at, updated_at)
                VALUES(?, ?, ?, ?, 'active', '', 0, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (self.id, self.title or "Agent session", self.cwd, self.model, stamp, stamp),
            )

    # -- construction ------------------------------------------------------

    @classmethod
    def create(
        cls,
        cwd: str | Path,
        *,
        title: str = "",
        model: str = "",
        session_id: Optional[str] = None,
        db_path: Optional[Path] = None,
    ) -> "AgentSession":
        return cls(
            session_id or str(uuid.uuid4()),
            cwd=str(cwd),
            title=title,
            model=model,
            db_path=db_path,
        )

    @classmethod
    def open(cls, session_id: str, *, db_path: Optional[Path] = None) -> "AgentSession":
        path = Path(db_path) if db_path else _default_db_path()
        if not path.is_file():
            raise SessionNotFound(f"no session database at {path}")
        with sqlite3.connect(path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT * FROM agent_sessions WHERE id = ?", (session_id,)
                ).fetchone()
            except sqlite3.OperationalError as exc:
                raise SessionNotFound(f"session {session_id} not found") from exc
        if row is None:
            raise SessionNotFound(f"session {session_id} not found")
        return cls(
            row["id"],
            cwd=row["cwd"],
            title=row["title"],
            model=row["model"],
            db_path=path,
        )

    @staticmethod
    def recent(limit: int = 20, *, db_path: Optional[Path] = None) -> List[SessionRecord]:
        path = Path(db_path) if db_path else _default_db_path()
        if not path.is_file():
            return []
        with sqlite3.connect(path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT * FROM agent_sessions ORDER BY updated_at DESC LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [
            SessionRecord(
                id=row["id"],
                title=row["title"],
                cwd=row["cwd"],
                model=row["model"],
                status=row["status"],
                turn_count=int(row["turn_count"]),
                created_at=int(row["created_at"]),
                updated_at=int(row["updated_at"]),
            )
            for row in rows
        ]

    # -- messages ----------------------------------------------------------

    def append_message(self, message: Dict[str, Any]) -> None:
        role = str(message.get("role") or "unknown")
        payload = json.dumps(message, ensure_ascii=False, default=str)
        with self._lock, self._connect() as conn:
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM agent_messages WHERE session_id = ?",
                (self.id,),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO agent_messages(id, session_id, seq, role, payload_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), self.id, seq, role, payload, _now_ms()),
            )
            conn.execute(
                "UPDATE agent_sessions SET updated_at = ? WHERE id = ?",
                (_now_ms(), self.id),
            )

    def messages(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM agent_messages WHERE session_id = ? ORDER BY seq ASC",
                (self.id,),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            try:
                out.append(json.loads(row["payload_json"]))
            except ValueError:
                continue
        return out

    def replace_messages(self, messages: Iterable[Dict[str, Any]]) -> None:
        """Rewrite the stored transcript after a compaction.

        Keeping the compacted form rather than the raw history is what makes
        resume cheap: opening a long session does not re-send everything it
        already summarised.
        """
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM agent_messages WHERE session_id = ?", (self.id,))
            for seq, message in enumerate(messages, start=1):
                conn.execute(
                    """
                    INSERT INTO agent_messages(id, session_id, seq, role, payload_json, created_at)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        self.id,
                        seq,
                        str(message.get("role") or "unknown"),
                        json.dumps(message, ensure_ascii=False, default=str),
                        _now_ms(),
                    ),
                )
            conn.execute(
                "UPDATE agent_sessions SET updated_at = ? WHERE id = ?",
                (_now_ms(), self.id),
            )

    # -- events ------------------------------------------------------------

    def log_event(self, kind: str, payload: Optional[Dict[str, Any]] = None) -> None:
        try:
            with self._lock, self._connect() as conn:
                seq = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM agent_events WHERE session_id = ?",
                    (self.id,),
                ).fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO agent_events(session_id, seq, kind, payload_json, created_at)
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        self.id,
                        seq,
                        str(kind),
                        json.dumps(payload or {}, ensure_ascii=False, default=str),
                        _now_ms(),
                    ),
                )
        except Exception:
            # Telemetry must never break the turn that produced it.
            pass

    def events(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT kind, payload_json, created_at FROM agent_events "
                "WHERE session_id = ? ORDER BY seq ASC",
                (self.id,),
            ).fetchall()
        out = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except ValueError:
                payload = {}
            out.append({"kind": row["kind"], "payload": payload, "at": int(row["created_at"])})
        return out

    # -- metadata ----------------------------------------------------------

    def set_status(self, status: str, *, turns: Optional[int] = None) -> None:
        with self._lock, self._connect() as conn:
            if turns is None:
                conn.execute(
                    "UPDATE agent_sessions SET status = ?, updated_at = ? WHERE id = ?",
                    (str(status), _now_ms(), self.id),
                )
            else:
                conn.execute(
                    "UPDATE agent_sessions SET status = ?, turn_count = ?, updated_at = ? WHERE id = ?",
                    (str(status), int(turns), _now_ms(), self.id),
                )

    def set_summary(self, summary: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE agent_sessions SET summary = ?, updated_at = ? WHERE id = ?",
                (summary or "", _now_ms(), self.id),
            )
