"""Local Model Adapter for OpenAI-compatible inference servers (ADR-018, Q2).

Supports Ollama, LM Studio, vLLM, and LocalAI via standard /v1 endpoints
using pure standard library urllib.request (zero external dependencies).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from core.agent_bridge.adapter import AgentAdapter
from core.agent_bridge.detectors import DetectionContext, detect_local_model_servers
from core.agent_bridge.errors import ConnectError
from core.agent_bridge.models import (
    AgentCallResult,
    AgentRecord,
    Capability,
    ProbeResult,
)


class LocalModelAdapter(AgentAdapter):
    """Adapter for local model inference servers (Ollama, LM Studio, vLLM)."""

    KIND = "local_model"
    TIER = 1

    def __init__(self, record: AgentRecord):
        super().__init__(record)
        self._endpoint = (record.endpoint or "http://127.0.0.1:11434/v1").rstrip("/")
        self._models: List[str] = []
        self._capabilities: List[Capability] = []

    @classmethod
    def detect(cls, ctx: DetectionContext) -> Optional[AgentRecord]:
        """Detect running local model servers on loopback ports."""
        records = detect_local_model_servers(ctx)
        return records[0] if records else None

    def _http_request(
        self,
        path: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """Perform an HTTP request to the local model server."""
        url = f"{self._endpoint}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Brahma-Agent-Bridge/1.0",
        }

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {err_body or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Connection failed to {url}: {exc.reason}") from exc

    def probe(self) -> ProbeResult:
        """3-stage probe: liveness, protocol, capabilities."""
        if not self._endpoint:
            return ProbeResult(ok=False, stage="liveness", detail="No endpoint configured")

        try:
            data = self._http_request("/models", method="GET", timeout=3.0)
            items = data.get("data") or data.get("models") or []
            models: List[str] = []
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and "id" in item:
                        models.append(str(item["id"]))
                    elif isinstance(item, str):
                        models.append(item)

            return ProbeResult(
                ok=True,
                stage="capability",
                detail=f"Local model server active: {len(models)} model(s) available",
                models=models,
            )
        except Exception as exc:
            return ProbeResult(ok=False, stage="protocol", detail=str(exc))

    def connect(self) -> None:
        """Probe the server and register model completion verbs."""
        probe_res = self.probe()
        if not probe_res.ok:
            self.record.status = "error"
            raise ConnectError(f"Failed to connect to local model server: {probe_res.detail}")

        self._models = probe_res.models
        default_model = self._models[0] if self._models else "default"

        # Register standard OpenAI-compatible completion capabilities
        self._capabilities = [
            Capability(
                name="chat",
                description=f"Generate a chat response using local model ({default_model}).",
                parameters={
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "The user query or prompt"},
                        "model": {
                            "type": "string",
                            "description": "Model identifier to invoke",
                            "default": default_model,
                        },
                        "temperature": {"type": "number", "default": 0.7},
                    },
                    "required": ["prompt"],
                },
                mutating=False,
            ),
        ]

        self.record.capabilities = [
            {"name": c.name, "description": c.description, "parameters": c.parameters}
            for c in self._capabilities
        ]
        self.record.status = "connected"

    def verbs(self) -> List[Capability]:
        return list(self._capabilities)

    def call(self, verb: str, args: Dict[str, Any]) -> AgentCallResult:
        """Invoke a completion or chat verb on the local model server."""
        if not self._capabilities:
            try:
                self.connect()
            except Exception as exc:
                return AgentCallResult(ok=False, output="", error=f"Not connected: {exc}")

        prompt = str(args.get("prompt") or "")
        model = str(args.get("model") or (self._models[0] if self._models else "default"))
        temp = float(args.get("temperature", 0.7))

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temp,
        }

        try:
            res = self._http_request("/chat/completions", method="POST", payload=payload, timeout=120.0)
            choices = res.get("choices") or []
            if not choices or not isinstance(choices, list):
                return AgentCallResult(ok=False, output="", error="No completion choices returned by model")

            first_choice = choices[0]
            msg = first_choice.get("message") or {}
            output_text = str(msg.get("content") or "")

            return AgentCallResult(
                ok=True,
                output=output_text,
                data=res,
                bytes_total=len(output_text.encode("utf-8")),
            )
        except Exception as exc:
            return AgentCallResult(ok=False, output="", error=str(exc))

    def close(self) -> None:
        """Idempotent teardown."""
        self.record.status = "found"
