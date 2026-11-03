"""
memory/connectors/letta.py
External memory connector for Letta (formerly MemGPT) tiered archival memory integration.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .base import BaseMemoryConnector, MemoryEntry

logger = logging.getLogger("LettaMemoryConnector")


class LettaMemoryConnector(BaseMemoryConnector):
    """Connector interfacing with Letta / MemGPT REST API server."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        agent_id: Optional[str] = None,
        token: Optional[str] = None,
    ):
        self._endpoint = (endpoint or os.environ.get("LETTA_ENDPOINT", "http://localhost:8283")).rstrip("/")
        self._agent_id = agent_id or os.environ.get("LETTA_AGENT_ID", "default")
        self._token = token or os.environ.get("LETTA_TOKEN", "")

    @property
    def backend_name(self) -> str:
        return "letta"

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
                return json.loads(raw) if raw else {}
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
        # Letta archival memory insertion endpoint
        path = f"/v1/agents/{self._agent_id}/archival"
        text = f"[{category.upper()}] {key}: {value}"
        res = self._request("POST", path, {"text": text})
        return res is not None

    def recall(self, query: str, limit: int = 8) -> List[MemoryEntry]:
        path = f"/v1/agents/{self._agent_id}/archival?query={urllib.request.quote(query)}&limit={limit}"
        res = self._request("GET", path)
        entries: List[MemoryEntry] = []
        if isinstance(res, list):
            for item in res:
                content = str(item.get("text") or item.get("content") or "")
                entries.append(
                    MemoryEntry(
                        key=str(item.get("id") or "letta_entry"),
                        value=content,
                        category="archival",
                        score=1.0,
                    )
                )
        return entries

    def delete(self, key: str, category: Optional[str] = None) -> bool:
        path = f"/v1/agents/{self._agent_id}/archival/{key}"
        res = self._request("DELETE", path)
        return res is not None

    def list_all(self, category: Optional[str] = None) -> List[MemoryEntry]:
        path = f"/v1/agents/{self._agent_id}/archival?limit=50"
        res = self._request("GET", path)
        entries: List[MemoryEntry] = []
        if isinstance(res, list):
            for item in res:
                entries.append(
                    MemoryEntry(
                        key=str(item.get("id") or ""),
                        value=str(item.get("text") or ""),
                        category="archival",
                    )
                )
        return entries

    def status(self) -> Dict[str, Any]:
        # Health check endpoint
        res = self._request("GET", "/v1/health")
        if res is not None:
            return {
                "ok": True,
                "backend": "letta",
                "available": True,
                "endpoint": self._endpoint,
                "detail": f"Connected to Letta server at {self._endpoint}",
            }
        return {
            "ok": False,
            "backend": "letta",
            "available": False,
            "endpoint": self._endpoint,
            "detail": f"Could not reach Letta server at {self._endpoint}",
        }
