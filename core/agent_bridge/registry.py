"""Persistent AgentRecord store and lifecycle management (ADR-018)."""

from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional

from core.agent_bridge.constants import AGENT_REGISTRY_FILENAME
from core.agent_bridge.models import AgentRecord
from core.user_paths import atomic_write_json, get_user_data_dir


def default_registry_path() -> Path:
    """Standard storage path for agent registry in user data."""
    return get_user_data_dir() / "config" / AGENT_REGISTRY_FILENAME


class AgentRegistry:
    """Thread-safe store for discovered and configured external agents."""

    def __init__(self, store_path: Optional[str | Path] = None):
        self._store_path = Path(store_path) if store_path is not None else default_registry_path()
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._store_path

    def load(self) -> Dict[str, AgentRecord]:
        """Read all agent records from disk. Returns empty dict on missing/corrupt store."""
        with self._lock:
            if not self._store_path.is_file():
                return {}
            try:
                raw_text = self._store_path.read_text(encoding="utf-8")
                parsed = json.loads(raw_text)
            except Exception:
                return {}

            items: Any = parsed
            if isinstance(parsed, dict) and "agents" in parsed:
                items = parsed.get("agents")

            records: Dict[str, AgentRecord] = {}
            if isinstance(items, dict):
                iterator = items.values()
            elif isinstance(items, list):
                iterator = items
            else:
                iterator = []

            for item in iterator:
                if not isinstance(item, dict):
                    continue
                try:
                    rec = AgentRecord.from_dict(item)
                    records[rec.id] = rec
                except Exception:
                    continue
            return records

    def save(self, records: Dict[str, AgentRecord] | AgentRecord | List[AgentRecord]) -> None:
        """Atomically persist records to disk."""
        with self._lock:
            if isinstance(records, AgentRecord):
                current = self.load()
                current[records.id] = records
                recs_to_save = current
            elif isinstance(records, list):
                recs_to_save = {r.id: r for r in records}
            else:
                recs_to_save = records
            data = {
                "version": 1,
                "agents": [rec.to_dict() for rec in recs_to_save.values()],
            }
            atomic_write_json(self._store_path, data)

    def get(self, agent_id: str) -> Optional[AgentRecord]:
        """Return the record for agent_id, or None if not found."""
        records = self.load()
        return records.get(agent_id)

    def list_all(self) -> List[AgentRecord]:
        """Return all agent records sorted deterministically by id."""
        records = self.load()
        return [records[k] for k in sorted(records.keys())]

    def register_or_update(self, new_record: AgentRecord) -> AgentRecord:
        """Merge a discovered or manually specified agent record with existing state."""
        with self._lock:
            records = self.load()
            existing = records.get(new_record.id)

            if existing is None:
                record_to_save = new_record
            else:
                # Merge evidence into existing record
                merged_sources = sorted(list(set(existing.sources + new_record.sources)))
                last_seen = max(existing.last_seen, new_record.last_seen, time.time())
                version = new_record.version if new_record.version else existing.version
                executable = new_record.executable if new_record.executable else existing.executable
                endpoint = new_record.endpoint if new_record.endpoint else existing.endpoint

                # If the user has explicitly edited this agent, preserve protected fields
                if existing.user_edited:
                    enabled = existing.enabled
                    display_name = existing.display_name
                    transport = existing.transport
                    manual = existing.manual
                else:
                    enabled = new_record.enabled
                    display_name = new_record.display_name or existing.display_name
                    transport = new_record.transport if new_record.transport.argv else existing.transport
                    manual = new_record.manual or existing.manual

                record_to_save = AgentRecord(
                    id=existing.id,
                    display_name=display_name,
                    kind=new_record.kind or existing.kind,
                    tier=new_record.tier or existing.tier,
                    status=existing.status if existing.status != "found" else new_record.status,
                    executable=executable,
                    endpoint=endpoint,
                    version=version,
                    transport=transport,
                    sources=merged_sources,
                    capabilities=new_record.capabilities or existing.capabilities,
                    manual=manual,
                    tier3=existing.tier3,
                    orchestrator=existing.orchestrator,
                    last_seen=last_seen,
                    enabled=enabled,
                    user_edited=existing.user_edited,
                )

            records[record_to_save.id] = record_to_save
            self.save(records)
            return record_to_save

    def rescan(self, detected: List[AgentRecord]) -> List[AgentRecord]:
        """Merge a batch of newly detected records into the registry and return all updated records."""
        with self._lock:
            for rec in detected:
                self.register_or_update(rec)
            return self.list_all()

    def set_enabled(self, agent_id: str, enabled: bool) -> bool:
        """Toggle an agent on or off. Marks user_edited=True so discovery won't flip it."""
        with self._lock:
            records = self.load()
            rec = records.get(agent_id)
            if rec is None:
                return False
            rec.enabled = bool(enabled)
            rec.user_edited = True
            records[agent_id] = rec
            self.save(records)
            return True

    def delete(self, agent_id: str) -> bool:
        """Remove an agent from the registry."""
        with self._lock:
            records = self.load()
            if agent_id not in records:
                return False
            del records[agent_id]
            self.save(records)
            return True

    def clear(self) -> None:
        """Wipe all registered agents."""
        with self._lock:
            self.save({})
