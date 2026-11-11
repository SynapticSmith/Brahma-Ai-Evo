"""
core/agent_bridge/adapters/remote.py — Tier 3 Remote & Decentralized Agent Connector (ADR-018, §4.12, §8.5).

Connects Brahma AI Evo to remote agents and decentralized runtimes over authenticated HTTPS/A2A
protocols behind AgentRuntime.execute with strict egress/SSRF defenses (IP/CIDR blocklists,
cloud metadata endpoint blocking), Windows DPAPI credential isolation, and per-verb mutation approval.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import ssl
import time
from typing import Any, Dict, List, Optional
import urllib.error
from urllib.parse import urlparse
import urllib.request

from core.agent_bridge.adapter import AgentAdapter
from core.agent_bridge.detectors import DetectionContext
from core.agent_bridge.errors import ForbiddenTarget
from core.agent_bridge.models import (
    AgentCallResult,
    AgentRecord,
    Capability,
    ProbeResult,
)

logger = logging.getLogger("RemoteAgentAdapter")

BLOCKED_METADATA_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.azure.com",
    "instance-data",
}


def validate_remote_url(url: str, *, allow_insecure: bool = False) -> None:
    """Validate remote agent endpoint against SSRF and cloud metadata access.

    Raises ForbiddenTarget if the target violates security policy.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise ForbiddenTarget(f"Malformed remote agent URL: {exc}") from exc

    if not parsed.scheme or parsed.scheme not in ("http", "https"):
        raise ForbiddenTarget(f"Invalid scheme '{parsed.scheme}': only https/http allowed.")

    if not allow_insecure and parsed.scheme != "https":
        raise ForbiddenTarget("Insecure HTTP forbidden for remote agents: HTTPS is required (allow_insecure=False).")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ForbiddenTarget("Missing hostname in remote agent endpoint.")

    if hostname in BLOCKED_METADATA_HOSTS:
        raise ForbiddenTarget(f"SSRF violation: access to cloud metadata service '{hostname}' is strictly forbidden.")

    # Check for private or link-local IP addresses
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_link_local:
            raise ForbiddenTarget(f"SSRF violation: link-local IP '{hostname}' is forbidden.")
        if not allow_insecure and (ip.is_private or ip.is_loopback):
            raise ForbiddenTarget(f"Private/loopback IP '{hostname}' requires explicit allow_insecure=True.")
    except ValueError:
        # Domain name
        if not allow_insecure and hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            raise ForbiddenTarget(f"Loopback hostname '{hostname}' requires explicit allow_insecure=True.")


class RemoteAgentAdapter(AgentAdapter):
    """Adapter for Tier 3 remote and decentralized agent runtimes."""

    KIND = "remote_http"
    TIER = 3

    def __init__(self, record: AgentRecord):
        super().__init__(record)
        tier3_cfg = record.tier3 or {}
        self._endpoint = (record.endpoint or tier3_cfg.get("base_url") or "").rstrip("/")
        self._allow_insecure = bool(tier3_cfg.get("allow_insecure", False))
        self._verify_ssl = bool(tier3_cfg.get("verify_ssl", True))
        self._pinned_spki = str(tier3_cfg.get("pinned_spki") or tier3_cfg.get("spki_sha256") or "").strip()
        self._node_identity: Optional[Any] = tier3_cfg.get("node_identity")

        # Auto-detect pairing and sign_requests:
        # If explicitly specified in tier3_cfg, respect it.
        # Otherwise, if pinned_spki or node_identity is set, or if endpoint matches a peer in PeerRegistry, enable signing.
        if "sign_requests" in tier3_cfg:
            self._sign_requests = bool(tier3_cfg["sign_requests"])
        else:
            self._sign_requests = bool(self._pinned_spki or self._node_identity or tier3_cfg.get("paired"))

        if not self._sign_requests and self._endpoint:
            try:
                from core.peer.pairing import PeerRegistry

                reg = tier3_cfg.get("peer_registry")
                if reg is None:
                    reg_path = tier3_cfg.get("registry_path")
                    reg = PeerRegistry(reg_path) if reg_path else PeerRegistry()
                peers_list = reg.list_peers() if hasattr(reg, "list_peers") else list(reg.load_peers().values())
                for peer in peers_list:
                    if peer.endpoint and peer.endpoint.rstrip("/") == self._endpoint:
                        self._sign_requests = True
                        if not self._pinned_spki and peer.spki_sha256:
                            self._pinned_spki = peer.spki_sha256
                        break
            except Exception as exc:
                logger.warning("Peer registry endpoint scan failed for endpoint %s: %s", self._endpoint, exc)

        self._capabilities: List[Capability] = []

    @classmethod
    def detect(cls, ctx: DetectionContext) -> Optional[AgentRecord]:
        """Detect remote agents configured in environment or settings."""
        from core.agent_bridge.detectors import detect_remote_agents

        recs = detect_remote_agents(ctx)
        return recs[0] if recs else None

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Brahma-Agent-Bridge-Tier3/1.0",
        }
        token: Optional[str] = None
        try:
            from core.secret_store import get_secret

            token = get_secret(f"remote_agent_{self.record.id}")
        except Exception:
            pass

        if not token:
            env_var = f"BRAHMA_AGENT_TOKEN_{self.record.id.upper().replace('-', '_')}"
            token = os.environ.get(env_var)

        if token:
            headers["Authorization"] = f"Bearer {token}"

        origin = None
        if self._node_identity:
            origin = getattr(self._node_identity, "node_id", None)
        else:
            try:
                from core.peer.identity import NodeIdentity

                origin = NodeIdentity.load_or_create(enforce_machine_binding=False).node_id
            except Exception:
                pass
        if origin:
            headers.setdefault("X-Brahma-Node", str(origin))

        return headers

    def _get_ssl_context(self) -> Optional[ssl.SSLContext]:
        if not self._verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        return None

    def probe(self) -> ProbeResult:
        """Diagnostic probe for remote agent health and agent-card metadata."""
        if not self._endpoint:
            return ProbeResult(ok=False, stage="liveness", detail="Remote agent endpoint is empty.")

        try:
            validate_remote_url(self._endpoint, allow_insecure=self._allow_insecure)
        except ForbiddenTarget as exc:
            return ProbeResult(ok=False, stage="liveness", detail=str(exc))

        probe_paths = ["/.well-known/agent.json", "/agent-card", "/health"]
        ssl_ctx = self._get_ssl_context()
        headers = self._get_headers()

        last_error = ""
        for path in probe_paths:
            url = f"{self._endpoint}{path}"
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=4.0, context=ssl_ctx) as resp:
                    if resp.status < 400:
                        raw = resp.read().decode("utf-8")
                        ver = "1.0.0"
                        try:
                            meta = json.loads(raw)
                            if isinstance(meta, dict) and "version" in meta:
                                ver = str(meta["version"])
                        except Exception:
                            pass
                        return ProbeResult(
                            ok=True,
                            stage="capability",
                            detail=f"Remote agent responsive at {url}",
                            version=ver,
                        )
            except Exception as exc:
                last_error = str(exc)
                continue

        return ProbeResult(
            ok=False,
            stage="liveness",
            detail=f"Could not reach remote agent at {self._endpoint}: {last_error}",
        )

    def connect(self) -> None:
        """Verify endpoint and register standard A2A agent verbs."""
        validate_remote_url(self._endpoint, allow_insecure=self._allow_insecure)
        self._capabilities = [
            Capability(
                name="run_task",
                description="Dispatch an autonomous task or prompt to the remote agent runtime.",
                parameters={
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Objective or instruction for remote agent"},
                        "context": {"type": "object", "description": "Optional parameters or structured data payload"},
                    },
                    "required": ["prompt"],
                },
                mutating=True,
            ),
            Capability(
                name="query_status",
                description="Query the real-time execution status or results of a task on the remote agent.",
                parameters={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "The remote task identifier to query"},
                    },
                    "required": ["task_id"],
                },
                mutating=False,
            ),
            Capability(
                name="cancel_task",
                description="Cancel an active in-flight task on the remote agent.",
                parameters={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "The remote task identifier to cancel"},
                    },
                    "required": ["task_id"],
                },
                mutating=True,
            ),
            Capability(
                name="fetch_artifact",
                description="Fetch a binary or text artifact generated by a remote task.",
                parameters={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "The remote task identifier"},
                        "artifact_id": {"type": "string", "description": "The content-addressed artifact identifier (e.g. sha256:...)"},
                    },
                    "required": ["task_id", "artifact_id"],
                },
                mutating=False,
            ),
        ]
        self.record.status = "connected"

    def verbs(self) -> List[Capability]:
        if not self._capabilities:
            self.connect()
        return self._capabilities

    def _http_request(self, path: str, method: str = "GET", data: Optional[dict] = None) -> AgentCallResult:
        validate_remote_url(self._endpoint, allow_insecure=self._allow_insecure)
        url = f"{self._endpoint}{path}"
        payload = json.dumps(data).encode("utf-8") if data is not None else None
        headers = self._get_headers()

        # Sign request with Ed25519 node key if requested
        if self._sign_requests:
            try:
                ident = self._node_identity
                if ident is None:
                    from core.peer.identity import NodeIdentity
                    ident = NodeIdentity.load_or_create(enforce_machine_binding=False)
                nonce = secrets.token_hex(16)
                timestamp = int(time.time())
                sig = ident.sign_request(
                    method=method,
                    path=path,
                    body=payload,
                    timestamp=timestamp,
                    nonce=nonce,
                )
                headers["X-Brahma-Node"] = ident.node_id
                headers["X-Brahma-Timestamp"] = str(timestamp)
                headers["X-Brahma-Nonce"] = nonce
                headers["X-Brahma-Signature"] = sig
            except Exception as exc:
                logger.debug("Could not sign remote request: %s", exc)

        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            if self._pinned_spki:
                from core.peer.crypto import build_pinned_opener
                opener = build_pinned_opener(self._pinned_spki)
                resp_ctx = opener.open(req, timeout=30.0)
            else:
                ssl_ctx = self._get_ssl_context()
                resp_ctx = urllib.request.urlopen(req, timeout=30.0, context=ssl_ctx)

            with resp_ctx as resp:
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
        """Execute verb on remote agent endpoint. Never raises for callers."""
        try:
            if verb == "run_task":
                task_data = dict(args)
                if not task_data.get("task_id"):
                    import uuid
                    tid = f"task-{uuid.uuid4().hex[:12]}"
                    task_data["task_id"] = tid
                    task_data.setdefault("trace_id", f"tr-{uuid.uuid4().hex[:12]}")
                    task_data.setdefault("root_task_id", tid)

                if not task_data.get("origin_node"):
                    origin = "local"
                    if self._node_identity:
                        origin = getattr(self._node_identity, "node_id", "local")
                    else:
                        try:
                            from core.peer.identity import NodeIdentity
                            origin = NodeIdentity.load_or_create(enforce_machine_binding=False).node_id
                        except Exception:
                            pass
                    task_data["origin_node"] = origin

                task_data.setdefault("coordinator_node", task_data["origin_node"])
                tier3_cfg = getattr(self.record, "tier3", {}) or {}
                task_data.setdefault("workspace", tier3_cfg.get("workspace") or "default")
                if "prompt" not in task_data and "message" in task_data:
                    task_data["prompt"] = task_data["message"]

                res = self._http_request("/tasks", method="POST", data=task_data)
                if bool(args.get("sync", False)) and res.ok and isinstance(res.data, dict):
                    task_id = str(res.data.get("task_id") or "").strip()
                    current_st = str(res.data.get("state") or res.data.get("status") or "")
                    if task_id and current_st in ("submitted", "queued", "working"):
                        deadline = min(float(args.get("deadline_s") or 120.0), 300.0)
                        start = time.time()
                        while (time.time() - start) < deadline:
                            time.sleep(0.5)
                            poll = self._http_request(f"/tasks/{task_id}", method="GET")
                            if not poll.ok:
                                break
                            st = str(poll.data.get("state") or poll.data.get("status") or "") if isinstance(poll.data, dict) else ""
                            if st in ("completed", "failed", "canceled", "rejected"):
                                return poll
                return res
            elif verb == "query_status":
                task_id = str(args.get("task_id") or "").strip()
                path = f"/tasks/{task_id}" if task_id else "/status"
                return self._http_request(path, method="GET")
            elif verb == "cancel_task":
                task_id = str(args.get("task_id") or "").strip()
                return self._http_request(f"/tasks/{task_id}/cancel", method="POST", data=args)
            elif verb == "fetch_artifact":
                task_id = str(args.get("task_id") or "").strip()
                art_id = str(args.get("artifact_id") or "").strip()
                return self._http_request(f"/tasks/{task_id}/artifacts/{art_id}", method="GET")
            else:
                return AgentCallResult(ok=False, output="", error=f"Unknown verb '{verb}' on remote agent")
        except ForbiddenTarget as exc:
            logger.warning("Remote agent target rejected: %s", exc)
            return AgentCallResult(ok=False, output="", error=str(exc))
        except Exception as exc:
            logger.exception("Error executing remote verb %s: %s", verb, exc)
            return AgentCallResult(ok=False, output="", error=str(exc))

    def close(self) -> None:
        """Idempotently close remote connection."""
        self._capabilities.clear()
        self.record.status = "found"
