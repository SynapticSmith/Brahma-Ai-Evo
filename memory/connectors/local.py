"""
memory/connectors/local.py
Built-in local memory connector (SQLite + JSON) for Brahma AI Evo.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base import BaseMemoryConnector, MemoryEntry

logger = logging.getLogger("LocalMemoryConnector")


class LocalMemoryConnector(BaseMemoryConnector):
    """Production-grade local memory engine using file-backed JSON and SQLite stores."""

    @property
    def backend_name(self) -> str:
        return "local"

    def store(
        self,
        key: str,
        value: Any,
        category: str = "notes",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        try:
            from memory import memory_manager

            val_str = str(value)
            memory_manager.remember(key, val_str, category=category)
            return True
        except Exception as exc:
            logger.error("Failed to store local memory (%s/%s): %s", category, key, exc)
            return False

    def recall(self, query: str, limit: int = 8) -> List[MemoryEntry]:
        entries: List[MemoryEntry] = []
        try:
            from memory import memory_manager

            raw_results = memory_manager.all_entries_for_ui()
            words = [w.lower() for w in query.split() if w.strip()]

            scored: List[tuple[float, Dict[str, Any]]] = []
            for r in raw_results:
                val = str(r.get("value", "")).lower()
                k = str(r.get("key", "")).lower()
                cat = str(r.get("category", "")).lower()

                score = 0.0
                if not words:
                    score = 1.0
                else:
                    for w in words:
                        if w in k:
                            score += 10.0
                        if w in val:
                            score += 4.0
                        if w in cat:
                            score += 1.0

                if score > 0.0:
                    scored.append((score, r))

            scored.sort(key=lambda t: t[0], reverse=True)
            for sc, r in scored[:limit]:
                entries.append(
                    MemoryEntry(
                        key=r.get("key", ""),
                        value=r.get("value", ""),
                        category=r.get("category", "notes"),
                        score=sc,
                        updated_at=r.get("updated", ""),
                    )
                )
        except Exception as exc:
            logger.error("Failed to recall local memory: %s", exc)

        return entries

    def delete(self, key: str, category: Optional[str] = None) -> bool:
        try:
            from memory import memory_manager

            mem = memory_manager.load_memory()
            deleted = False
            for cat_name, items in mem.items():
                if category and cat_name != category:
                    continue
                if isinstance(items, dict) and key in items:
                    del items[key]
                    deleted = True
            if deleted:
                memory_manager.save_memory(mem)
            return deleted
        except Exception as exc:
            logger.error("Failed to delete local memory entry %s: %s", key, exc)
            return False

    def list_all(self, category: Optional[str] = None) -> List[MemoryEntry]:
        entries: List[MemoryEntry] = []
        try:
            from memory import memory_manager

            raw_entries = memory_manager.all_entries_for_ui()
            for r in raw_entries:
                cat = r.get("category", "notes")
                if category and cat != category:
                    continue
                entries.append(
                    MemoryEntry(
                        key=r.get("key", ""),
                        value=r.get("value", ""),
                        category=cat,
                        updated_at=r.get("updated", ""),
                    )
                )
        except Exception as exc:
            logger.error("Failed to list local memory: %s", exc)
        return entries

    def status(self) -> Dict[str, Any]:
        try:
            from memory import memory_manager

            entries = memory_manager.all_entries_for_ui()
            return {
                "ok": True,
                "backend": "local",
                "total_entries": len(entries),
                "mode": "offline_first",
                "detail": f"Local memory active ({len(entries)} items stored)",
            }
        except Exception as exc:
            return {
                "ok": False,
                "backend": "local",
                "total_entries": 0,
                "detail": f"Local memory error: {exc}",
            }
