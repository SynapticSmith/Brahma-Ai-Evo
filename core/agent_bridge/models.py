"""Data models for the Agent & Orchestrator Bridge (ADR-018)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Capability:
    """A single advertised capability/verb of an external agent."""

    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    mutating: bool = False


@dataclass
class ProbeResult:
    """Result of a multi-stage diagnostic probe."""

    ok: bool
    stage: str  # "liveness" | "protocol" | "capability"
    detail: str
    version: Optional[str] = None
    models: List[str] = field(default_factory=list)


@dataclass
class AgentCallResult:
    """Outcome of invoking a capability on an agent."""

    ok: bool
    output: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    timed_out: bool = False
    truncated: bool = False
    bytes_total: int = 0
    artifact_ref: Optional[str] = None


@dataclass
class AgentTransport:
    """Transport parameters for communicating with an agent."""

    mode: str = "stdio"  # stdio | mcp_stdio | http_openai | http_mcp | ws | remote_http
    argv: List[str] = field(default_factory=list)
    cwd: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)  # Env variable names, never secrets!


@dataclass
class AgentRecord:
    """Persisted descriptor of a discovered or configured agent."""

    id: str
    display_name: str
    kind: str  # local_model | mcp_server | cli_agent | agent_daemon | peer | orchestrator
    tier: int = 1  # 1 | 2 | 3
    status: str = "found"  # found | connecting | connected | degraded | needs_login | disabled | error
    executable: Optional[str] = None
    endpoint: Optional[str] = None
    version: Optional[str] = None
    transport: AgentTransport = field(default_factory=AgentTransport)
    sources: List[str] = field(default_factory=list)
    capabilities: List[Dict[str, Any]] = field(default_factory=list)
    manual: Optional[str] = None
    tier3: Dict[str, Any] = field(
        default_factory=lambda: {"allow_insecure": False, "verify_ssl": True, "base_url": None}
    )
    orchestrator: Dict[str, Any] = field(
        default_factory=lambda: {"connector": None, "high_leverage_verbs": []}
    )
    last_seen: float = field(default_factory=time.time)
    enabled: bool = True
    user_edited: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentRecord:
        raw = dict(data)
        transport_raw = raw.get("transport")
        if isinstance(transport_raw, dict):
            raw["transport"] = AgentTransport(**transport_raw)
        elif not isinstance(transport_raw, AgentTransport):
            raw["transport"] = AgentTransport()

        # Handle defaults & migrations
        if "kind" not in raw or not raw["kind"]:
            raw["kind"] = "agent"
        if "display_name" not in raw or not raw["display_name"]:
            raw["display_name"] = raw.get("id", "agent")
        if "tier" not in raw or not isinstance(raw["tier"], int):
            raw["tier"] = 1
        if "status" not in raw or not raw["status"]:
            raw["status"] = "found"
        if "sources" not in raw or not isinstance(raw["sources"], list):
            raw["sources"] = []
        if "capabilities" not in raw or not isinstance(raw["capabilities"], list):
            raw["capabilities"] = []
        if "enabled" not in raw:
            raw["enabled"] = True
        if "user_edited" not in raw:
            raw["user_edited"] = False

        known_fields = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in raw.items() if k in known_fields}
        return cls(**filtered)
