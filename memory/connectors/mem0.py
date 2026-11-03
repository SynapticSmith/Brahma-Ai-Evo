"""
memory/connectors/mem0.py
External memory connector for Mem0 (embedchain/mem0ai) integration.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from .base import BaseMemoryConnector, MemoryEntry

logger = logging.getLogger("Mem0MemoryConnector")


class Mem0MemoryConnector(BaseMemoryConnector):
    """Connector interfacing with Mem0 hybrid vector memory service or local Qdrant/Chroma instance."""

    def __init__(self, api_key: Optional[str] = None, user_id: str = "brahma_default"):
        self._api_key = api_key or os.environ.get("MEM0_API_KEY", "")
        self._user_id = user_id
        self._client: Optional[Any] = None

    @property
    def backend_name(self) -> str:
        return "mem0"

    def _ensure_client(self) -> Optional[Any]:
        if self._client is not None:
            return self._client
        try:
            from mem0 import Memory

            config = {}
            if self._api_key:
                config["api_key"] = self._api_key
            self._client = Memory.from_config(config) if config else Memory()
            return self._client
        except ImportError:
            logger.info("Mem0 package 'mem0ai' not installed.")
            return None
        except Exception as exc:
            logger.warning("Failed to initialize Mem0 client: %s", exc)
            return None

    def store(
        self,
        key: str,
        value: Any,
        category: str = "notes",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        client = self._ensure_client()
        if client is None:
            return False
        try:
            meta = dict(metadata or {})
            meta["key"] = key
            meta["category"] = category
            text = f"{key}: {value}"
            client.add(text, user_id=self._user_id, metadata=meta)
            return True
        except Exception as exc:
            logger.error("Mem0 store failed for %s: %s", key, exc)
            return False

    def recall(self, query: str, limit: int = 8) -> List[MemoryEntry]:
        client = self._ensure_client()
        if client is None:
            return []
        entries: List[MemoryEntry] = []
        try:
            results = client.search(query, user_id=self._user_id, limit=limit)
            for item in (results or []):
                val = item.get("memory") or item.get("text") or str(item)
                meta = item.get("metadata") or {}
                entries.append(
                    MemoryEntry(
                        key=meta.get("key") or str(item.get("id") or "mem0_entry"),
                        value=val,
                        category=meta.get("category", "notes"),
                        score=float(item.get("score") or 1.0),
                        metadata=meta,
                    )
                )
        except Exception as exc:
            logger.error("Mem0 search failed for '%s': %s", query, exc)
        return entries

    def delete(self, key: str, category: Optional[str] = None) -> bool:
        client = self._ensure_client()
        if client is None:
            return False
        try:
            client.delete(memory_id=key)
            return True
        except Exception as exc:
            logger.error("Mem0 delete failed for %s: %s", key, exc)
            return False

    def list_all(self, category: Optional[str] = None) -> List[MemoryEntry]:
        client = self._ensure_client()
        if client is None:
            return []
        entries: List[MemoryEntry] = []
        try:
            raw = client.get_all(user_id=self._user_id)
            for item in (raw or []):
                val = item.get("memory") or str(item)
                meta = item.get("metadata") or {}
                entries.append(
                    MemoryEntry(
                        key=meta.get("key") or str(item.get("id") or ""),
                        value=val,
                        category=meta.get("category", "notes"),
                        metadata=meta,
                    )
                )
        except Exception as exc:
            logger.error("Mem0 list_all failed: %s", exc)
        return entries

    def status(self) -> Dict[str, Any]:
        has_pkg = False
        try:
            import mem0  # noqa: F401
            has_pkg = True
        except ImportError:
            pass

        if not has_pkg:
            return {
                "ok": False,
                "backend": "mem0",
                "available": False,
                "detail": "mem0ai package not installed (run `pip install mem0ai`)",
            }

        client = self._ensure_client()
        if client is None:
            return {
                "ok": False,
                "backend": "mem0",
                "available": False,
                "detail": "Mem0 client failed to initialize",
            }

        return {
            "ok": True,
            "backend": "mem0",
            "available": True,
            "mode": "cloud" if self._api_key else "local_vector",
            "detail": "Mem0 hybrid memory backend connected",
        }
