"""Peer Capability Model and Advisory Scheduling (ADR-035 & Deep Dive §3, §8.1).

Defines peer capability cards, label matching, hard requirements filtering,
and advisory node placement scoring.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

ALLOWED_PROFILE_HIERARCHY = {
    "chat": 0,
    "reader": 1,
    "worker": 2,
    "operator": 3,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class PeerCapabilityCard:
    """Advertised capabilities, resource limits, and health of a peer node."""

    node_id: str
    name: str = "Brahma Node"
    spki_sha256: str = ""
    endpoint: str = ""
    profiles: List[str] = field(default_factory=lambda: ["reader", "worker"])
    workspaces: List[str] = field(default_factory=list)
    labels: Set[str] = field(default_factory=set)
    verbs: List[str] = field(
        default_factory=lambda: ["run_task", "query_status", "cancel_task", "fetch_artifact"]
    )
    limits: Dict[str, Any] = field(
        default_factory=lambda: {
            "max_turns": 12,
            "wall_clock_s": 300.0,
            "max_body_bytes": 1_048_576,
        }
    )
    active_tasks: int = 0
    max_concurrent_tasks: int = 2
    updated_at: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        if isinstance(self.labels, dict):
            self.labels = set(self.labels.keys())
        elif not isinstance(self.labels, set):
            self.labels = set(self.labels)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["labels"] = sorted(self.labels)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PeerCapabilityCard:
        raw_labels = data.get("labels") or []
        labels_set = set(raw_labels) if isinstance(raw_labels, (list, tuple, set)) else set()
        return cls(
            node_id=str(data.get("node_id") or ""),
            name=str(data.get("name") or "Brahma Node"),
            spki_sha256=str(data.get("spki_sha256") or ""),
            profiles=list(data.get("profiles") or ["reader", "worker"]),
            workspaces=list(data.get("workspaces") or []),
            labels=labels_set,
            verbs=list(data.get("verbs") or ["run_task", "query_status", "cancel_task", "fetch_artifact"]),
            limits=dict(data.get("limits") or {}),
            active_tasks=int(data.get("active_tasks", 0)),
            max_concurrent_tasks=int(data.get("max_concurrent_tasks", 2)),
            updated_at=str(data.get("updated_at") or now_iso()),
        )


def matches_requirements(
    card: PeerCapabilityCard,
    *,
    required_profile: Optional[str] = None,
    required_workspace: Optional[str] = None,
    required_labels: Optional[Iterable[str]] = None,
    required_verbs: Optional[Iterable[str]] = None,
) -> bool:
    """Check whether a peer card satisfies hard execution requirements.
    
    Hard criteria:
    1. Node must support the requested profile (or higher).
    2. Node must possess the requested workspace alias.
    3. Node must have all required labels.
    4. Node must support all required verbs.
    5. Node must have capacity (active_tasks < max_concurrent_tasks).
    """
    if card.active_tasks >= card.max_concurrent_tasks:
        return False

    if required_profile:
        req_p = required_profile.strip().lower()
        node_profiles = [p.strip().lower() for p in card.profiles]
        req_level = ALLOWED_PROFILE_HIERARCHY.get(req_p, 0)
        has_profile = any(ALLOWED_PROFILE_HIERARCHY.get(np, 0) >= req_level for np in node_profiles)
        if not has_profile:
            return False

    if required_workspace:
        req_ws = required_workspace.strip().lower()
        node_workspaces = [w.strip().lower() for w in card.workspaces]
        if req_ws not in node_workspaces and "default" not in node_workspaces:
            return False

    if required_labels:
        req_set = {str(lbl).strip().lower() for lbl in required_labels}
        card_set = {str(lbl).strip().lower() for lbl in card.labels}
        if not req_set.issubset(card_set):
            return False

    if required_verbs:
        req_v = {str(v).strip().lower() for v in required_verbs}
        card_v = {str(v).strip().lower() for v in card.verbs}
        if not req_v.issubset(card_v):
            return False

    return True


def score_peer(
    card: PeerCapabilityCard,
    *,
    preferred_node: Optional[str] = None,
    preferred_labels: Optional[Iterable[str]] = None,
) -> float:
    """Calculate an advisory placement score for a peer node. Higher is better."""
    score = 100.0

    # 1. Operator override / human pin has highest weight (Idea I-02)
    if preferred_node and card.node_id.lower() == preferred_node.lower():
        score += 1000.0

    # 2. Prefer nodes with lower active load
    load_ratio = card.active_tasks / max(1, card.max_concurrent_tasks)
    score -= load_ratio * 40.0

    # 3. Soft label preferences
    if preferred_labels:
        card_labels = {str(lbl).strip().lower() for lbl in card.labels}
        for pref in preferred_labels:
            if str(pref).strip().lower() in card_labels:
                score += 15.0

    return max(0.0, score)


def select_best_peer(
    candidates: List[PeerCapabilityCard],
    *,
    required_profile: Optional[str] = None,
    required_workspace: Optional[str] = None,
    required_labels: Optional[Iterable[str]] = None,
    required_verbs: Optional[Iterable[str]] = None,
    preferred_node: Optional[str] = None,
    preferred_labels: Optional[Iterable[str]] = None,
) -> Optional[PeerCapabilityCard]:
    """Filter candidate peers against hard criteria, then rank by advisory score."""
    eligible = [
        c for c in candidates
        if matches_requirements(
            c,
            required_profile=required_profile,
            required_workspace=required_workspace,
            required_labels=required_labels,
            required_verbs=required_verbs,
        )
    ]
    if not eligible:
        return None

    return max(
        eligible,
        key=lambda c: score_peer(
            c,
            preferred_node=preferred_node,
            preferred_labels=preferred_labels,
        ),
    )
