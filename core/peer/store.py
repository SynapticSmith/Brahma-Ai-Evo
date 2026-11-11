"""Isolated SQLite registry for peer tasks (ADR-030, ADR-032, Invariant S-04).

Enforces (origin_node, task_id) idempotency, manages leases, and tracks task execution.
This database is strictly isolated from sessions.db and is never shared across nodes.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, Iterator, List, Optional, Tuple, Iterable

from core.peer.model import BrahmaTaskRequest, now_iso
from core.peer.state import WireState, is_terminal, validate_transition


class IdempotencyConflictError(ValueError):
    """Raised when a task_id is reused with a different request body hash."""


def default_peer_db_path() -> Path:
    """Resolve the platform peer tasks SQLite path."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data) / "BrahmaAI" / "peer"
    else:
        base = Path.home() / ".brahma" / "peer"
    base.mkdir(parents=True, exist_ok=True)
    return base / "tasks.db"


class PeerTaskStore:
    """Thread-safe SQLite store for BTE tasks."""

    TABLE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS peer_tasks (
      task_id             TEXT NOT NULL,
      origin_node         TEXT NOT NULL,
      token_id            TEXT,
      request_hash        TEXT NOT NULL,
      trace_id            TEXT NOT NULL,
      context_id          TEXT,
      parent_task_id      TEXT,
      root_task_id        TEXT NOT NULL,
      coordinator_node    TEXT NOT NULL,
      peer_node           TEXT NOT NULL,
      state               TEXT NOT NULL,
      effect              TEXT NOT NULL,
      profile             TEXT NOT NULL,
      workspace           TEXT NOT NULL,
      attempt             INTEGER NOT NULL DEFAULT 1,
      prompt_sha256       TEXT NOT NULL,
      prompt_bytes        INTEGER NOT NULL,
      result_json         TEXT,
      result_bytes        INTEGER,
      error_code          TEXT,
      error_json          TEXT,
      created_at          TEXT NOT NULL,
      accepted_at         TEXT,
      started_at          TEXT,
      finished_at         TEXT,
      lease_expires_at    TEXT,
      heartbeat_at        TEXT,
      cancel_requested_at TEXT,
      audit_sequence      INTEGER,
      audit_hash          TEXT,
      PRIMARY KEY (origin_node, task_id)
    );
    """

    INDEXES = """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_peer_task_origin_id
      ON peer_tasks(origin_node, task_id);

    CREATE INDEX IF NOT EXISTS ix_peer_task_state_lease
      ON peer_tasks(state, lease_expires_at);

    CREATE INDEX IF NOT EXISTS ix_peer_task_trace
      ON peer_tasks(trace_id);
    """

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        if db_path is None:
            self.db_path = default_peer_db_path()
        elif str(db_path) == ":memory:":
            self.db_path = ":memory:"
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _init_db(self) -> None:
        with self._lock, self._connection() as conn:
            conn.executescript(self.TABLE_SCHEMA)
            cur = conn.execute("PRAGMA table_info(peer_tasks)")
            cols = {row["name"] for row in cur.fetchall()}
            expected_columns = {
                "token_id": "TEXT",
                "result_json": "TEXT",
                "result_bytes": "INTEGER",
                "error_code": "TEXT",
                "error_json": "TEXT",
                "started_at": "TEXT",
                "finished_at": "TEXT",
                "lease_expires_at": "TEXT",
                "heartbeat_at": "TEXT",
                "cancel_requested_at": "TEXT",
                "audit_sequence": "INTEGER",
                "audit_hash": "TEXT",
            }
            for col_name, col_type in expected_columns.items():
                if col_name not in cols:
                    conn.execute(f"ALTER TABLE peer_tasks ADD COLUMN {col_name} {col_type}")
            conn.executescript(self.INDEXES)
            conn.commit()

    def record_task(
        self,
        request: BrahmaTaskRequest,
        peer_node: str,
        *,
        attempt: int = 1,
        token_id: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Record an incoming task request durably.

        Returns (is_existing, task_dict).
        If (origin_node, task_id) exists:
          - If request_hash matches: returns existing row (idempotent).
          - If request_hash differs: raises IdempotencyConflictError.
        """
        req_hash = request.request_hash()
        import hashlib
        prompt_sha = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()
        prompt_len = len(request.prompt.encode("utf-8"))
        accepted = now_iso()

        with self._lock, self._connection() as conn:
            cur = conn.execute(
                "SELECT * FROM peer_tasks WHERE origin_node = ? AND task_id = ?",
                (request.origin_node, request.task_id),
            )
            row = cur.fetchone()
            if row is not None:
                stored = dict(row)
                if stored["request_hash"] != req_hash:
                    raise IdempotencyConflictError(
                        f"idempotency_conflict: task_id {request.task_id} already exists with different request body"
                    )
                return True, stored

            conn.execute(
                """
                INSERT INTO peer_tasks (
                    task_id, origin_node, token_id, request_hash, trace_id, context_id,
                    parent_task_id, root_task_id, coordinator_node, peer_node,
                    state, effect, profile, workspace, attempt, prompt_sha256,
                    prompt_bytes, created_at, accepted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.task_id,
                    request.origin_node,
                    token_id,
                    req_hash,
                    request.trace_id,
                    request.context_id,
                    request.parent_task_id,
                    request.root_task_id,
                    request.coordinator_node,
                    peer_node,
                    WireState.SUBMITTED.value,
                    request.effect,
                    request.profile,
                    request.workspace,
                    attempt,
                    prompt_sha,
                    prompt_len,
                    request.created_at,
                    accepted,
                ),
            )
            conn.commit()

            cur = conn.execute(
                "SELECT * FROM peer_tasks WHERE origin_node = ? AND task_id = ?",
                (request.origin_node, request.task_id),
            )
            return False, dict(cur.fetchone())

    def get_task(self, origin_node: str, task_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve task record by origin and id."""
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                "SELECT * FROM peer_tasks WHERE origin_node = ? AND task_id = ?",
                (origin_node, task_id),
            )
            row = cur.fetchone()
            return dict(row) if row is not None else None

    def get_task_by_id(
        self,
        task_id: str,
        caller_token_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve task record by task_id alone.

        If caller_token_id is provided, prefers the task created by that token on collision.
        """
        with self._lock, self._connection() as conn:
            if caller_token_id:
                cur = conn.execute(
                    "SELECT * FROM peer_tasks WHERE task_id = ? AND token_id = ? ORDER BY accepted_at DESC LIMIT 1",
                    (task_id, caller_token_id),
                )
                row = cur.fetchone()
                if row is not None:
                    return dict(row)
            cur = conn.execute(
                "SELECT * FROM peer_tasks WHERE task_id = ? ORDER BY accepted_at DESC LIMIT 1",
                (task_id,),
            )
            row = cur.fetchone()
            return dict(row) if row is not None else None

    def update_state(
        self,
        origin_node: str,
        task_id: str,
        new_state: str,
        *,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        result_json: Optional[str] = None,
        error_code: Optional[str] = None,
        error_json: Optional[str] = None,
        lease_ttl_s: Optional[float] = None,
    ) -> bool:
        """Update task state, checking transition validity and immutability."""
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                "SELECT state FROM peer_tasks WHERE origin_node = ? AND task_id = ?",
                (origin_node, task_id),
            )
            row = cur.fetchone()
            if row is None:
                return False

            current_state = row["state"]
            if not validate_transition(current_state, new_state):
                return False

            now = now_iso()
            fields = ["state = ?"]
            params: List[Any] = [new_state.lower()]

            if new_state.lower() == WireState.WORKING.value and not started_at:
                fields.append("started_at = ?")
                params.append(now)
            elif started_at:
                fields.append("started_at = ?")
                params.append(started_at)

            if is_terminal(new_state):
                fields.append("finished_at = ?")
                params.append(finished_at or now)

            if result_json is not None:
                fields.append("result_json = ?")
                params.append(result_json)
                fields.append("result_bytes = ?")
                params.append(len(result_json.encode("utf-8")))

            if error_code is not None:
                fields.append("error_code = ?")
                params.append(error_code)
            if error_json is not None:
                fields.append("error_json = ?")
                params.append(error_json)

            if lease_ttl_s is not None and lease_ttl_s > 0:
                expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_ttl_s)).isoformat()
                fields.append("lease_expires_at = ?")
                params.append(expires)
                fields.append("heartbeat_at = ?")
                params.append(now)

            params.extend([origin_node, task_id])
            query = f"UPDATE peer_tasks SET {', '.join(fields)} WHERE origin_node = ? AND task_id = ?"
            conn.execute(query, params)
            conn.commit()
            return True

    def heartbeat(self, origin_node: str, task_id: str, lease_ttl_s: float = 30.0) -> bool:
        """Extend the lease of an actively working task."""
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=lease_ttl_s)).isoformat()
        now_str = now.isoformat()

        with self._lock, self._connection() as conn:
            cur = conn.execute(
                """
                UPDATE peer_tasks
                SET lease_expires_at = ?, heartbeat_at = ?
                WHERE origin_node = ? AND task_id = ? AND state = ?
                """,
                (expires, now_str, origin_node, task_id, WireState.WORKING.value),
            )
            conn.commit()
            return cur.rowcount > 0

    def request_cancel(self, origin_node: str, task_id: str) -> Optional[Dict[str, Any]]:
        """Request task cancellation. If submitted, transitions directly to canceled."""
        now = now_iso()
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                "SELECT state FROM peer_tasks WHERE origin_node = ? AND task_id = ?",
                (origin_node, task_id),
            )
            row = cur.fetchone()
            if row is None:
                return None

            current_state = row["state"]
            if current_state == WireState.SUBMITTED.value:
                conn.execute(
                    """
                    UPDATE peer_tasks
                    SET state = ?, cancel_requested_at = ?, finished_at = ?
                    WHERE origin_node = ? AND task_id = ?
                    """,
                    (WireState.CANCELED.value, now, now, origin_node, task_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE peer_tasks
                    SET cancel_requested_at = ?
                    WHERE origin_node = ? AND task_id = ?
                    """,
                    (now, origin_node, task_id),
                )
            conn.commit()

            cur = conn.execute(
                "SELECT * FROM peer_tasks WHERE origin_node = ? AND task_id = ?",
                (origin_node, task_id),
            )
            return dict(cur.fetchone())

    def list_active_tasks(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List tasks that are currently submitted or working."""
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM peer_tasks
                WHERE state IN ('submitted', 'working')
                ORDER BY accepted_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]

    def list_expired_leases(self, grace_s: float = 0.0) -> List[Dict[str, Any]]:
        """Find working tasks whose lease has expired."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=grace_s)).isoformat()
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM peer_tasks
                WHERE state = ? AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
                """,
                (WireState.WORKING.value, cutoff),
            )
            return [dict(r) for r in cur.fetchall()]

    def reconcile_expired_leases(
        self,
        grace_s: float = 0.0,
        exclude_task_ids: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Find working tasks whose lease has expired and transition them to UNKNOWN."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=grace_s)).isoformat()
        exclude = set(exclude_task_ids or ())
        expired_tasks: List[Dict[str, Any]] = []
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM peer_tasks
                WHERE state = ? AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
                """,
                (WireState.WORKING.value, cutoff),
            )
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                if r["task_id"] in exclude:
                    continue
                conn.execute(
                    """
                    UPDATE peer_tasks
                    SET state = ?, error_code = 'lease_expired'
                    WHERE origin_node = ? AND task_id = ? AND state = ?
                    """,
                    (WireState.UNKNOWN.value, r["origin_node"], r["task_id"], WireState.WORKING.value),
                )
                r["state"] = WireState.UNKNOWN.value
                r["error_code"] = "lease_expired"
                expired_tasks.append(r)
            if expired_tasks:
                conn.commit()
        return expired_tasks
