"""Brahma Task Envelope (BTE-1) data models, validation, and canonical hashing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, List, Optional
import uuid

BTE_VERSION = "1.0"
MAX_PROMPT_BYTES = 1_000_000      # 1 MiB prompt limit
MAX_CONTEXT_BYTES = 262_144      # 256 KiB context limit
DEFAULT_INLINE_MAX_BYTES = 65_536  # 64 KiB inline result limit
MAX_ARTIFACT_BYTES = 33_554_432   # 32 MiB artifact limit

ALLOWED_EFFECTS = ("none", "idempotent", "mutating")
ALLOWED_PROFILES = ("chat", "reader", "worker", "operator")


def now_iso() -> str:
    """Return ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def new_task_id() -> str:
    """Generate a stable UUID string."""
    return str(uuid.uuid4())


def canonical_json_bytes(obj: Any) -> bytes:
    """Serialize an object to canonical deterministic JSON bytes."""
    return json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_request_hash(data: Dict[str, Any]) -> str:
    """Compute SHA-256 over canonical BTE request body excluding transport headers/signatures."""
    # Filter out transport-level, timestamp, or signature fields if present
    filtered = {
        k: v for k, v in data.items()
        if k not in (
            "signature",
            "authorization",
            "x_brahma_signature",
            "x_brahma_nonce",
            "created_at",
        )
    }
    return hashlib.sha256(canonical_json_bytes(filtered)).hexdigest()


@dataclass(slots=True)
class BrahmaTaskRequest:
    task_id: str
    trace_id: str
    root_task_id: str
    origin_node: str
    coordinator_node: str
    prompt: str
    bte: str = BTE_VERSION
    context_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    hop: int = 0
    created_at: str = field(default_factory=now_iso)
    deadline_s: float = 300.0
    effect: str = "none"
    profile: str = "reader"
    workspace: str = "brahma"
    context: Dict[str, Any] = field(default_factory=dict)
    input_artifacts: List[Dict[str, Any]] = field(default_factory=list)
    budget: Dict[str, Any] = field(default_factory=lambda: {"max_turns": 12, "max_tool_calls": 40})
    reply: Dict[str, Any] = field(default_factory=lambda: {"mode": "final", "inline_max_bytes": DEFAULT_INLINE_MAX_BYTES})
    labels: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate envelope fields and sizes against BTE-1 specification."""
        if not self.bte.startswith("1."):
            raise ValueError(f"version_unsupported: BTE version {self.bte!r} not supported")
        if not self.task_id or not self.task_id.strip():
            raise ValueError("invalid_request: missing task_id")
        if not self.trace_id or not self.trace_id.strip():
            raise ValueError("invalid_request: missing trace_id")
        if not self.origin_node or not self.origin_node.strip():
            raise ValueError("invalid_request: missing origin_node")
        if not self.coordinator_node or not self.coordinator_node.strip():
            raise ValueError("invalid_request: missing coordinator_node")

        # Effect validation (defaults to mutating if unrecognized)
        if self.effect not in ALLOWED_EFFECTS:
            raise ValueError(f"invalid_request: effect must be one of {ALLOWED_EFFECTS}, got {self.effect!r}")

        # Profile validation
        if self.profile not in ALLOWED_PROFILES:
            raise ValueError(f"invalid_request: profile must be one of {ALLOWED_PROFILES}, got {self.profile!r}")

        # Workspace validation: must be an alias, never path traversal or absolute path
        ws = (self.workspace or "").strip()
        if not ws:
            raise ValueError("invalid_request: missing workspace alias")
        if ".." in ws or "/" in ws or "\\" in ws or ":" in ws:
            raise ValueError(f"invalid_request: workspace must be a simple alias, got {self.workspace!r}")

        # Size limits
        prompt_bytes = len(self.prompt.encode("utf-8"))
        if prompt_bytes > MAX_PROMPT_BYTES:
            raise ValueError(f"invalid_request: prompt size ({prompt_bytes} bytes) exceeds limit ({MAX_PROMPT_BYTES} bytes)")

        context_bytes = len(canonical_json_bytes(self.context))
        if context_bytes > MAX_CONTEXT_BYTES:
            raise ValueError(f"invalid_request: context size ({context_bytes} bytes) exceeds limit ({MAX_CONTEXT_BYTES} bytes)")

        # Deadlines & budgets
        if self.deadline_s <= 0:
            raise ValueError("invalid_request: deadline_s must be positive")
        if self.hop < 0:
            raise ValueError("invalid_request: hop must be non-negative")

    def request_hash(self) -> str:
        return canonical_request_hash(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BrahmaTaskRequest":
        req = cls(
            task_id=str(data.get("task_id") or ""),
            trace_id=str(data.get("trace_id") or ""),
            root_task_id=str(data.get("root_task_id") or data.get("task_id") or ""),
            origin_node=str(data.get("origin_node") or ""),
            coordinator_node=str(data.get("coordinator_node") or data.get("origin_node") or ""),
            prompt=str(data.get("prompt") or ""),
            bte=str(data.get("bte") or BTE_VERSION),
            context_id=data.get("context_id"),
            parent_task_id=data.get("parent_task_id"),
            hop=int(data.get("hop", 0)),
            created_at=str(data.get("created_at") or now_iso()),
            deadline_s=float(data.get("deadline_s", 300.0)),
            effect=str(data.get("effect") or "mutating").strip().lower(),
            profile=str(data.get("profile") or "reader").strip().lower(),
            workspace=str(data.get("workspace") or "brahma").strip(),
            context=dict(data.get("context") or {}),
            input_artifacts=list(data.get("input_artifacts") or []),
            budget=dict(data.get("budget") or {"max_turns": 12, "max_tool_calls": 40}),
            reply=dict(data.get("reply") or {"mode": "final", "inline_max_bytes": DEFAULT_INLINE_MAX_BYTES}),
            labels=dict(data.get("labels") or {}),
        )
        req.validate()
        return req


@dataclass(slots=True)
class BrahmaTaskError:
    code: str
    message: str
    retryable: bool = False
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BrahmaTaskResult:
    task_id: str
    state: str
    bte: str = BTE_VERSION
    attempt: int = 1
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=lambda: {"turns": 0, "tool_calls": 0, "tokens": 0, "denials": 0})
    partial: bool = False
    timed_out: bool = False
    error: Optional[BrahmaTaskError] = None
    audit: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        if self.error:
            res["error"] = self.error.to_dict()
        return res

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BrahmaTaskResult":
        err_data = data.get("error")
        err = BrahmaTaskError(**err_data) if err_data else None
        return cls(
            task_id=str(data.get("task_id") or ""),
            state=str(data.get("state") or ""),
            bte=str(data.get("bte") or BTE_VERSION),
            attempt=int(data.get("attempt", 1)),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            result=data.get("result"),
            artifacts=list(data.get("artifacts") or []),
            usage=dict(data.get("usage") or {}),
            partial=bool(data.get("partial", False)),
            timed_out=bool(data.get("timed_out", False)),
            error=err,
            audit=data.get("audit"),
        )
