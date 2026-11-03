"""
core/agent_bridge/adapters/orchestrator.py — Tier 2 Orchestrator Adapter (ADR-018, §4.11, §8.4).

Connects Brahma AI to local/external agent fleet orchestrators (Paperclip, Orca ADE)
behind AgentRuntime.execute with strict self-target recursion guards, golden-fixture
drift tolerance, and per-verb mutation approval gating.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import urllib.error
from urllib.parse import urlparse
import urllib.request
from typing import Any, Dict, List, Optional

from agent.process import run_captured
from core.agent_bridge.adapter import AgentAdapter
from core.agent_bridge.detectors import DetectionContext
from core.agent_bridge.errors import ForbiddenTarget
from core.agent_bridge.models import (
    AgentCallResult,
    AgentRecord,
    Capability,
    ProbeResult,
)

logger = logging.getLogger("OrchestratorAdapter")


class OrchestratorAdapter(AgentAdapter):
    """Adapter for Tier 2 agent orchestrators (Paperclip, Orca)."""

    KIND = "orchestrator"
    TIER = 2

    def __init__(self, record: AgentRecord):
        super().__init__(record)
        orch = record.orchestrator or {}
        connector = (orch.get("connector") or "paperclip").lower()
        self._connector = connector
        self._endpoint = (record.endpoint or "http://127.0.0.1:3100/api").rstrip("/")
        self._executable = record.executable or "orca"
        self._capabilities: List[Capability] = []

    @classmethod
    def detect(cls, ctx: DetectionContext) -> Optional[AgentRecord]:
        """Detect presence of known orchestrator services or CLIs."""
        from core.agent_bridge.detectors import detect_orchestrators
        recs = detect_orchestrators(ctx)
        return recs[0] if recs else None

    def _check_self_target(self, endpoint: str) -> None:
        """Reject self-target loopback attempts against Brahma's own gateway (ADR-013, ADR-018)."""
        try:
            parsed = urlparse(endpoint)
            gateway_port = int(os.environ.get("BRAHMA_GATEWAY_PORT", 8000))
            if parsed.hostname in ("127.0.0.1", "localhost", "0.0.0.0") and parsed.port == gateway_port:
                raise ForbiddenTarget(
                    f"Self-target loopback rejected: Orchestrator cannot target Brahma's own gateway on port {gateway_port}."
                )
        except ForbiddenTarget:
            raise
        except Exception:
            pass

    def probe(self) -> ProbeResult:
        """Probe orchestrator health and protocol status."""
        if self._connector == "paperclip":
            try:
                self._check_self_target(self._endpoint)
            except ForbiddenTarget as exc:
                return ProbeResult(ok=False, stage="liveness", detail=str(exc))
            # Stage 1: Liveness probe
            try:
                url = f"{self._endpoint}/health"
                req = urllib.request.Request(url, headers={"User-Agent": "Brahma-Agent-Bridge/1.0"})
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    if resp.status < 400:
                        return ProbeResult(
                            ok=True,
                            stage="capability",
                            detail=f"Paperclip API responsive at {self._endpoint}",
                            version="1.0.0",
                        )
            except Exception as exc:
                # Fallback probe to root
                try:
                    req_root = urllib.request.Request(self._endpoint, headers={"User-Agent": "Brahma-Agent-Bridge/1.0"})
                    with urllib.request.urlopen(req_root, timeout=2.5) as resp:
                        if resp.status < 400:
                            return ProbeResult(
                                ok=True,
                                stage="capability",
                                detail=f"Paperclip API root responsive at {self._endpoint}",
                                version="1.0.0",
                            )
                except Exception:
                    pass
                return ProbeResult(
                    ok=False,
                    stage="liveness",
                    detail=f"Could not reach Paperclip API at {self._endpoint}: {exc}",
                )

        elif self._connector == "orca":
            try:
                resolved = shutil.which(self._executable) or self._executable
                if not Path(resolved).exists() and not shutil.which(resolved):
                    return ProbeResult(
                        ok=False,
                        stage="liveness",
                        detail=f"Orca binary not found: {self._executable}",
                    )
                cmd_str = f'"{resolved}" --version'
                res = run_captured(cmd_str, cwd=Path.cwd(), timeout=4.0)
                if res.returncode == 0:
                    ver = res.stdout.strip().splitlines()[0] if res.stdout else "unknown"
                    return ProbeResult(
                        ok=True,
                        stage="capability",
                        detail=f"Orca ADE responsive ({ver})",
                        version=ver,
                    )
                return ProbeResult(
                    ok=False,
                    stage="protocol",
                    detail=f"Orca --version returned non-zero ({res.returncode}): {res.stderr[:200]}",
                )
            except Exception as exc:
                return ProbeResult(
                    ok=False,
                    stage="liveness",
                    detail=f"Orca probe error: {exc}",
                )

        return ProbeResult(ok=False, stage="liveness", detail=f"Unknown orchestrator connector: {self._connector}")

    def connect(self) -> None:
        """Verify endpoint and register standard verbs."""
        if self._connector == "paperclip":
            self._check_self_target(self._endpoint)
            self._capabilities = [
                Capability(
                    name="list_agents",
                    description="List active worker agents managed by the Paperclip fleet.",
                    parameters={"type": "object", "properties": {}},
                    mutating=False,
                ),
                Capability(
                    name="get_status",
                    description="Get overall fleet health, active issues, and orchestrator status.",
                    parameters={"type": "object", "properties": {}},
                    mutating=False,
                ),
                Capability(
                    name="create_issue",
                    description="Dispatch a new issue/task to the Paperclip orchestrator fleet.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Short title of the task or issue"},
                            "description": {"type": "string", "description": "Detailed task instructions"},
                            "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"], "default": "medium"},
                        },
                        "required": ["title"],
                    },
                    mutating=True,
                ),
                Capability(
                    name="run_heartbeat",
                    description="Trigger an automated execution heartbeat pulse across the fleet.",
                    parameters={"type": "object", "properties": {}},
                    mutating=True,
                ),
            ]
            self.record.status = "connected"

        elif self._connector == "orca":
            self._capabilities = [
                Capability(
                    name="list_worktrees",
                    description="List isolated worktree environments in Orca ADE.",
                    parameters={"type": "object", "properties": {}},
                    mutating=False,
                ),
                Capability(
                    name="assign_goal",
                    description="Assign an autonomous coding goal to an Orca worktree.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "goal": {"type": "string", "description": "The coding objective to assign"},
                            "worktree": {"type": "string", "description": "Optional worktree target"},
                        },
                        "required": ["goal"],
                    },
                    mutating=True,
                ),
            ]
            self.record.status = "connected"

    def verbs(self) -> List[Capability]:
        if not self._capabilities:
            self.connect()
        return self._capabilities

    def _http_call(self, path: str, method: str = "GET", data: Optional[dict] = None) -> AgentCallResult:
        self._check_self_target(self._endpoint)
        url = f"{self._endpoint}{path}"
        payload = json.dumps(data).encode("utf-8") if data is not None else None
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Brahma-Agent-Bridge/1.0",
        }
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                raw = resp.read().decode("utf-8")
                parsed_data = {}
                try:
                    if raw.strip().startswith(("{", "[")):
                        parsed_data = json.loads(raw)
                except Exception:
                    pass
                return AgentCallResult(
                    ok=True,
                    output=raw,
                    data=parsed_data if isinstance(parsed_data, dict) else {"items": parsed_data},
                    bytes_total=len(raw),
                )
        except urllib.error.HTTPError as exc:
            err_msg = exc.read().decode("utf-8", errors="replace")
            return AgentCallResult(ok=False, output="", error=f"HTTP {exc.code}: {err_msg}")
        except Exception as exc:
            return AgentCallResult(ok=False, output="", error=str(exc))

    def call(self, verb: str, args: Dict[str, Any]) -> AgentCallResult:
        """Execute verb on the orchestrator. Never raises exceptions for caller."""
        try:
            if self._connector == "paperclip":
                if verb == "list_agents":
                    return self._http_call("/agents", method="GET")
                elif verb == "get_status":
                    return self._http_call("/status", method="GET")
                elif verb == "create_issue":
                    return self._http_call("/issues", method="POST", data=args)
                elif verb == "run_heartbeat":
                    return self._http_call("/heartbeat", method="POST", data=args)
                else:
                    return AgentCallResult(ok=False, output="", error=f"Unknown verb '{verb}' for Paperclip")

            elif self._connector == "orca":
                resolved = shutil.which(self._executable) or self._executable
                if verb == "list_worktrees":
                    cmd_str = f'"{resolved}" worktrees --json'
                    res = run_captured(cmd_str, cwd=Path.cwd(), timeout=15.0)
                    return AgentCallResult(
                        ok=res.returncode == 0,
                        output=res.stdout if res.returncode == 0 else "",
                        error=res.stderr if res.returncode != 0 else None,
                        bytes_total=len(res.stdout),
                    )
                elif verb == "assign_goal":
                    import shlex
                    goal = str(args.get("goal") or "")
                    parts = [f'"{resolved}"', "assign", f"--goal {shlex.quote(goal)}"]
                    if args.get("worktree"):
                        parts.append(f"--worktree {shlex.quote(str(args['worktree']))}")
                    cmd_str = " ".join(parts)
                    res = run_captured(cmd_str, cwd=Path.cwd(), timeout=30.0)
                    return AgentCallResult(
                        ok=res.returncode == 0,
                        output=res.stdout if res.returncode == 0 else "",
                        error=res.stderr if res.returncode != 0 else None,
                        bytes_total=len(res.stdout),
                    )
                else:
                    return AgentCallResult(ok=False, output="", error=f"Unknown verb '{verb}' for Orca")

            return AgentCallResult(ok=False, output="", error=f"Unsupported connector: {self._connector}")

        except ForbiddenTarget as exc:
            logger.warning("Orchestrator target forbidden: %s", exc)
            return AgentCallResult(ok=False, output="", error=str(exc))
        except Exception as exc:
            logger.exception("Error executing verb %s on %s", verb, self.record.id)
            return AgentCallResult(ok=False, output="", error=str(exc))

    def close(self) -> None:
        """Idempotently close orchestrator connection."""
        self._capabilities.clear()
        self.record.status = "found"
