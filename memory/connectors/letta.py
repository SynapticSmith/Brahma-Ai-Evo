"""
memory/connectors/letta.py
External memory connector for Letta (formerly MemGPT) tiered archival memory integration.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import BaseMemoryConnector, MemoryEntry

logger = logging.getLogger("LettaMemoryConnector")


class LettaMemoryConnector(BaseMemoryConnector):
    """Connector interfacing with Letta / MemGPT REST API server and local MemFS."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        agent_id: Optional[str] = None,
        token: Optional[str] = None,
    ):
        keys = {}
        try:
            from core.secret_store import load_api_keys
            keys = load_api_keys()
        except Exception:
            pass
        self._endpoint = (endpoint or os.environ.get("LETTA_ENDPOINT") or keys.get("letta_endpoint") or "http://localhost:8283").rstrip("/")
        explicit_agent = agent_id or os.environ.get("LETTA_AGENT_ID") or keys.get("letta_agent_id")
        self._agent_id = explicit_agent or self._discover_local_agent_id() or "default"
        self._token = token or os.environ.get("LETTA_TOKEN") or keys.get("letta_token") or ""

    @property
    def backend_name(self) -> str:
        return "letta"

    def _discover_local_agent_id(self) -> Optional[str]:
        """Auto-detect the local Letta agent ID from ~/.letta/settings.json or ~/.letta/lc-local-backend."""
        try:
            letta_dir = Path.home() / ".letta"
            settings_path = letta_dir / "settings.json"
            if settings_path.is_file():
                data = json.loads(settings_path.read_text(encoding="utf-8"))
                last = data.get("lastAgent")
                if last:
                    return str(last)
                agents = data.get("agents", [])
                if agents and isinstance(agents, list):
                    first_id = agents[0].get("agentId")
                    if first_id:
                        return str(first_id)

            memfs_dir = letta_dir / "lc-local-backend" / "memfs"
            if memfs_dir.is_dir():
                for sub in memfs_dir.iterdir():
                    if sub.is_dir() and sub.name.startswith("agent-"):
                        return sub.name
        except Exception:
            pass
        return None

    def _get_memfs_memory_dir(self) -> Optional[Path]:
        """Return the path to the local agent MemFS memory directory if it exists."""
        if not self._agent_id or self._agent_id == "default":
            disc = self._discover_local_agent_id()
            if disc:
                self._agent_id = disc
        if self._agent_id and self._agent_id != "default":
            p = Path.home() / ".letta" / "lc-local-backend" / "memfs" / self._agent_id / "memory"
            if p.is_dir():
                return p
        return None

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Optional[Any]:
        url = f"{self._endpoint}{path}"
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                if not raw or not raw.strip():
                    return {}
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return {"status": raw.strip(), "ok": True}
        except Exception as exc:
            logger.debug("Letta request failed (%s %s): %s", method, url, exc)
            return None

    def store(
        self,
        key: str,
        value: Any,
        category: str = "notes",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        stored_any = False
        text = f"[{category.upper()}] {key}: {value}"
        payload = {"text": text}

        # 1. Try REST endpoints (Cloud / Docker)
        for path in (
            f"/v1/agents/{self._agent_id}/archival-memory",
            f"/v1/agents/{self._agent_id}/archival",
        ):
            res = self._request("POST", path, payload)
            if res is not None:
                stored_any = True
                break

        # 2. Local MemFS persistence (offline local Letta App Server)
        mem_dir = self._get_memfs_memory_dir()
        if mem_dir:
            try:
                arch_file = mem_dir / "archival.json"
                data = {}
                if arch_file.is_file():
                    try:
                        data = json.loads(arch_file.read_text(encoding="utf-8"))
                    except Exception:
                        data = {}
                data[key] = {
                    "value": str(value),
                    "category": category,
                    "metadata": metadata or {},
                }
                arch_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

                # If category is personal identity/preferences, also record in human.md
                if category in ("identity", "preferences", "notes"):
                    human_md = mem_dir / "human.md"
                    if human_md.is_file():
                        content = human_md.read_text(encoding="utf-8")
                        entry_line = f"- {category.title()}: {key} = {value}"
                        if entry_line not in content:
                            content = content.rstrip() + f"\n{entry_line}\n"
                            human_md.write_text(content, encoding="utf-8")
                stored_any = True
            except Exception as e:
                logger.debug("Local MemFS store failed: %s", e)

        return stored_any

    def recall(self, query: str, limit: int = 8) -> List[MemoryEntry]:
        # 1. Try REST search endpoint
        q = urllib.request.quote(query)
        res = self._request("GET", f"/v1/agents/{self._agent_id}/archival-memory/search?query={q}&top_k={limit}")
        if res is None:
            res = self._request("GET", f"/v1/agents/{self._agent_id}/archival?query={q}&limit={limit}")

        items = []
        if isinstance(res, list):
            items = res
        elif isinstance(res, dict):
            items = res.get("results") or res.get("passages") or []

        entries: List[MemoryEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or item.get("text") or "")
            entry_id = str(item.get("id") or "letta_entry")
            score = float(item.get("score") or 1.0)
            entries.append(
                MemoryEntry(
                    key=entry_id,
                    value=content,
                    category="archival",
                    score=score,
                )
            )

        if entries:
            return entries[:limit]

        # 2. Local MemFS fallback search
        mem_dir = self._get_memfs_memory_dir()
        if mem_dir:
            try:
                arch_file = mem_dir / "archival.json"
                if arch_file.is_file():
                    data = json.loads(arch_file.read_text(encoding="utf-8"))
                    query_words = [w for w in re.split(r"[^a-zA-Z0-9]+", (query or "").lower()) if len(w) > 1]
                    matched = []
                    for k, val_info in data.items():
                        v_str = str(val_info.get("value") or "")
                        cat = str(val_info.get("category") or "notes")
                        score = 0.0
                        for qw in query_words:
                            if qw in k.lower():
                                score += 2.0
                            if qw in v_str.lower():
                                score += 1.0
                            if qw in cat.lower():
                                score += 0.5
                        if score > 0 or not query_words:
                            matched.append(MemoryEntry(
                                key=k,
                                value=f"[{cat.upper()}] {k}: {v_str}",
                                category=cat,
                                score=score or 1.0,
                            ))
                    matched.sort(key=lambda e: e.score, reverse=True)
                    return matched[:limit]
            except Exception as e:
                logger.debug("Local MemFS recall failed: %s", e)

        return []

    def delete(self, key: str, category: Optional[str] = None) -> bool:
        deleted = False
        for path in (
            f"/v1/agents/{self._agent_id}/archival-memory/{key}",
            f"/v1/agents/{self._agent_id}/archival/{key}",
        ):
            res = self._request("DELETE", path)
            if res is not None:
                deleted = True
                break

        mem_dir = self._get_memfs_memory_dir()
        if mem_dir:
            try:
                arch_file = mem_dir / "archival.json"
                if arch_file.is_file():
                    data = json.loads(arch_file.read_text(encoding="utf-8"))
                    if key in data:
                        del data[key]
                        arch_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                        deleted = True
            except Exception:
                pass

        return deleted

    def list_all(self, category: Optional[str] = None) -> List[MemoryEntry]:
        for path in (
            f"/v1/agents/{self._agent_id}/archival-memory?limit=50",
            f"/v1/agents/{self._agent_id}/archival?limit=50",
        ):
            res = self._request("GET", path)
            if res is not None:
                items = res if isinstance(res, list) else (res.get("results") or res.get("passages") or [])
                if items:
                    return [
                        MemoryEntry(
                            key=str(item.get("id") or ""),
                            value=str(item.get("content") or item.get("text") or ""),
                            category="archival",
                        )
                        for item in items
                        if isinstance(item, dict)
                    ]

        mem_dir = self._get_memfs_memory_dir()
        if mem_dir:
            try:
                arch_file = mem_dir / "archival.json"
                if arch_file.is_file():
                    data = json.loads(arch_file.read_text(encoding="utf-8"))
                    return [
                        MemoryEntry(
                            key=k,
                            value=f"[{v.get('category', 'notes').upper()}] {k}: {v.get('value', '')}",
                            category=v.get("category", "notes"),
                        )
                        for k, v in data.items()
                        if isinstance(v, dict)
                    ]
            except Exception:
                pass
        return []

    def status(self) -> Dict[str, Any]:
        # Health check endpoint (checks /healthz, /readyz, /v1/health, and /v1/health/)
        server_ok = False
        for path in ("/healthz", "/readyz", "/v1/health", "/v1/health/"):
            res = self._request("GET", path)
            if res is not None:
                server_ok = True
                break

        mem_dir = self._get_memfs_memory_dir()
        memfs_ok = mem_dir is not None

        if server_ok or memfs_ok:
            count = len(self.list_all())
            return {
                "ok": True,
                "backend": "letta",
                "available": True,
                "endpoint": self._endpoint,
                "agent_id": self._agent_id,
                "entries_count": count,
                "detail": f"Connected to Letta server at {self._endpoint} (agent: {self._agent_id})",
            }
        return {
            "ok": False,
            "backend": "letta",
            "available": False,
            "endpoint": self._endpoint,
            "agent_id": self._agent_id,
            "detail": f"Could not reach Letta server at {self._endpoint}",
        }
