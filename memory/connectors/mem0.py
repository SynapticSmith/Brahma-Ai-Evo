"""
memory/connectors/mem0.py
External memory connector for Mem0 (embedchain/mem0ai) hybrid vector memory integration.
Supports both zero-cloud local vector search (Qdrant + FastEmbed) and cloud Mem0 platform.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any, Dict, List, Optional

from .base import BaseMemoryConnector, MemoryEntry

logger = logging.getLogger("Mem0MemoryConnector")


class Mem0MemoryConnector(BaseMemoryConnector):
    """Connector interfacing with Mem0 hybrid vector memory service (local Qdrant or cloud)."""

    def __init__(self, api_key: Optional[str] = None, user_id: str = "brahma_default"):
        if not api_key:
            try:
                from core.secret_store import load_api_keys
                api_key = load_api_keys().get("mem0_api_key")
            except Exception:
                pass
        self._api_key = (api_key or os.environ.get("MEM0_API_KEY", "")).strip()
        self._user_id = user_id
        self._client: Optional[Any] = None
        self._is_cloud: bool = bool(self._api_key and self._api_key.startswith("m0-"))

    @property
    def backend_name(self) -> str:
        return "mem0"

    def _ensure_client(self) -> Optional[Any]:
        if self._client is not None:
            return self._client

        try:
            # 1. Cloud mode: If api_key begins with 'm0-', use MemoryClient
            if self._is_cloud:
                try:
                    from mem0 import MemoryClient
                    self._client = MemoryClient(api_key=self._api_key)
                    logger.info("Initialized Mem0 Cloud MemoryClient")
                    return self._client
                except Exception as exc:
                    logger.warning("Failed to initialize Mem0 Cloud client: %s", exc)

            # 2. Local mode: Embedded Qdrant vector store + FastEmbed ONNX embeddings
            from mem0 import Memory
            from core.user_paths import get_user_data_dir

            qdrant_path = str(get_user_data_dir() / "memory" / "mem0_qdrant")
            os.makedirs(qdrant_path, exist_ok=True)

            config = {
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "path": qdrant_path,
                        "embedding_model_dims": 384,
                        "on_disk": True,
                    },
                },
                "embedder": {
                    "provider": "fastembed",
                    "config": {
                        "model": "BAAI/bge-small-en-v1.5",
                    },
                },
                "llm": {
                    "provider": "openai",
                    "config": {
                        "api_key": "local_offline",
                    },
                },
            }

            try:
                self._client = Memory.from_config(config)
            except Exception as init_exc:
                if "1536" in str(init_exc) or "dimension" in str(init_exc).lower():
                    logger.warning("Resetting stale Qdrant vector collection: %s", init_exc)
                    shutil.rmtree(qdrant_path, ignore_errors=True)
                    os.makedirs(qdrant_path, exist_ok=True)
                    self._client = Memory.from_config(config)
                else:
                    raise

            self._is_cloud = False
            logger.info("Initialized local Mem0 Memory instance (Qdrant + FastEmbed)")
            return self._client

        except ImportError as ie:
            logger.info("Mem0 dependencies not fully installed: %s", ie)
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
            text = f"[{category.upper()}] {key}: {value}"

            if self._is_cloud:
                client.add(text, user_id=self._user_id, metadata=meta)
            else:
                # In local mode, bypass LLM inference extraction (facts are already extracted by Brahma)
                try:
                    client.add(text, user_id=self._user_id, metadata=meta, infer=False)
                except TypeError:
                    client.add(text, user_id=self._user_id, metadata=meta)
            return True
        except Exception as exc:
            # Self-healing on legacy vector dimension mismatch
            if "dimension" in str(exc).lower() and not self._is_cloud:
                try:
                    from core.user_paths import get_user_data_dir
                    q_path = str(get_user_data_dir() / "memory" / "mem0_qdrant")
                    logger.warning("Resetting mismatched vector store on dimension error...")
                    self._client = None
                    shutil.rmtree(q_path, ignore_errors=True)
                    client = self._ensure_client()
                    if client is not None:
                        client.add(text, user_id=self._user_id, metadata=meta, infer=False)
                        return True
                except Exception as retry_exc:
                    logger.error("Mem0 store retry failed: %s", retry_exc)
            logger.error("Mem0 store failed for %s: %s", key, exc)
            return False

    def recall(self, query: str, limit: int = 8) -> List[MemoryEntry]:
        client = self._ensure_client()
        if client is None:
            return []
        entries: List[MemoryEntry] = []
        try:
            if self._is_cloud:
                raw_results = client.search(query, user_id=self._user_id, limit=limit)
            else:
                try:
                    raw_results = client.search(query, filters={"user_id": self._user_id}, limit=limit)
                except (TypeError, ValueError):
                    raw_results = client.search(query, user_id=self._user_id, limit=limit)

            items = raw_results.get("results") if isinstance(raw_results, dict) else raw_results
            for item in (items or []):
                if not isinstance(item, dict):
                    continue
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
        return entries[:limit]

    def delete(self, key: str, category: Optional[str] = None) -> bool:
        client = self._ensure_client()
        if client is None:
            return False
        try:
            # 1. Try deleting directly by memory ID
            try:
                client.delete(memory_id=key)
                return True
            except Exception:
                pass

            # 2. Look for matching entry by key in all entries
            all_entries = self.list_all(category=category)
            deleted = False
            for entry in all_entries:
                if entry.key == key:
                    entry_id = entry.metadata.get("id") or entry.metadata.get("memory_id")
                    if entry_id:
                        client.delete(memory_id=str(entry_id))
                        deleted = True
            return deleted
        except Exception as exc:
            logger.error("Mem0 delete failed for %s: %s", key, exc)
            return False

    def list_all(self, category: Optional[str] = None) -> List[MemoryEntry]:
        client = self._ensure_client()
        if client is None:
            return []
        entries: List[MemoryEntry] = []
        try:
            if self._is_cloud:
                raw = client.get_all(user_id=self._user_id)
            else:
                try:
                    raw = client.get_all(filters={"user_id": self._user_id})
                except (TypeError, ValueError):
                    raw = client.get_all(user_id=self._user_id)

            items = raw.get("results") if isinstance(raw, dict) else raw
            for item in (items or []):
                if not isinstance(item, dict):
                    continue
                val = item.get("memory") or str(item)
                meta = item.get("metadata") or {}
                meta_cat = meta.get("category", "notes")
                if category and meta_cat != category:
                    continue
                meta_copy = dict(meta)
                if "id" in item:
                    meta_copy["id"] = item["id"]
                entries.append(
                    MemoryEntry(
                        key=meta.get("key") or str(item.get("id") or ""),
                        value=val,
                        category=meta_cat,
                        metadata=meta_copy,
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

        mode_name = "Cloud Platform (mem0.ai)" if self._is_cloud else "Local Vector (Qdrant + FastEmbed)"
        count = 0
        try:
            count = len(self.list_all())
        except Exception:
            pass

        return {
            "ok": True,
            "backend": "mem0",
            "available": True,
            "mode": "cloud" if self._is_cloud else "local",
            "entries_count": count,
            "detail": f"Mem0 hybrid memory backend connected ({mode_name})",
        }
