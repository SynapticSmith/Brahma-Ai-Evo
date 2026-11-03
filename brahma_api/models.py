"""Model identity, and the capability preset it selects.

Clients demand a ``model`` string, and it is the only per-request knob every
client reliably sends. So it carries the security-relevant choice:

    brahma-evo[<:variant>][@<workspace-alias>]

The string is never forwarded upstream. The upstream provider and model come
from the token, which is the only place a human chose them.

Two independent axes meet here. A **lane** says whose tools run (Brahma's, the
client's, or both); a **profile** says what Brahma's tools may do. Phase 0 ships
the agent lane only, with three model ids: ``brahma-evo``, ``brahma-evo:read``
and ``brahma-evo:plan``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.permissions import ALLOW, DENY, PermissionPolicy, default_policy_dict

from .auth import Principal
from .config import MODEL_CREATED, BrahmaApiConfig
from .errors import forbidden, not_found

#: variant -> (lane, profile override). An empty variant is the default agent.
VARIANTS: Dict[str, Tuple[str, Optional[str]]] = {
    "": ("agent", None),
    "read": ("agent", "reader"),
    "plan": ("plan", "reader"),
    # Phase 2/3. Parsed so the error can name them, refused until implemented.
    "passthrough": ("passthrough", None),
    "hybrid": ("hybrid", None),
}
IMPLEMENTED_LANES = ("agent", "plan")
LISTED_MODELS = ("brahma-evo", "brahma-evo:read", "brahma-evo:plan")


@dataclass(frozen=True)
class ParsedModel:
    raw: str
    variant: str
    lane: str
    profile_override: Optional[str]
    workspace_alias: Optional[str]


@dataclass(frozen=True)
class ResolvedModel:
    model_id: str
    lane: str
    profile: str
    workspace_alias: Optional[str]
    workspace: Optional[Path]
    tools: bool
    max_turns: int


def parse_model_id(raw: str) -> Optional[ParsedModel]:
    """Parse the grammar. Returns ``None`` for anything malformed.

    A malformed id and an unknown id get the same 404 on purpose: telling a
    caller that ``brahma-evo:shell`` is *shaped* correctly but not implemented
    invites probing, and no client needs the distinction.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    base, _, alias = text.partition("@")
    workspace_alias = alias.strip() or None
    if workspace_alias is not None:
        if len(workspace_alias) > 32 or not all(
            ch.isalnum() or ch in "-_" for ch in workspace_alias
        ):
            return None
    name, _, variant = base.partition(":")
    if name != "brahma-evo":
        return None
    variant = variant.strip().lower()
    if ":" in base and not variant:
        # "brahma-evo:" is not "brahma-evo"; a trailing separator is malformed.
        return None
    if variant not in VARIANTS:
        return None
    lane, override = VARIANTS[variant]
    return ParsedModel(text, variant, lane, override, workspace_alias)


def _intersect(principal: Principal, override: Optional[str]) -> str:
    """The token's preset can only narrow, never widen.

    A `chat` key stays tool-free whatever model id it asks for; a `reader` key
    cannot become a writer by naming a model. The reverse (a `worker` key asking
    for `brahma-evo:read`) is a deliberate downgrade and is allowed.
    """
    base = principal.profile
    if base == "chat":
        return "chat"
    if override is None:
        return base
    order = {"chat": 0, "reader": 1, "worker": 2, "operator": 3}
    return override if order[override] < order[base] else base


def resolve(
    config: BrahmaApiConfig,
    principal: Principal,
    raw_model: str,
) -> ResolvedModel:
    parsed = parse_model_id(raw_model)
    if parsed is None:
        raise not_found(
            f"The model {raw_model!r} does not exist. "
            f"Available models: {', '.join(LISTED_MODELS)}."
        )
    if parsed.lane not in IMPLEMENTED_LANES:
        raise not_found(
            f"The model {parsed.raw!r} is not available in this build. "
            f"Available models: {', '.join(LISTED_MODELS)}."
        )

    alias = parsed.workspace_alias or principal.workspace
    if parsed.workspace_alias and parsed.workspace_alias != principal.workspace:
        raise forbidden(
            f"This key is bound to workspace {principal.workspace!r}; "
            f"it cannot select {parsed.workspace_alias!r}."
        )

    profile = _intersect(principal, parsed.profile_override)
    if profile == "chat":
        alias = None
    workspace = config.workspace_path(alias) if alias else None
    if profile != "chat":
        if workspace is None:
            raise forbidden(
                f"The workspace alias {alias!r} is not configured. "
                "Add it to config/brahma_api.json:workspaces.",
                code="workspace_not_allowed",
            )
        if not workspace.is_dir():
            raise forbidden(
                f"The workspace for alias {alias!r} does not exist on this machine.",
                code="workspace_not_allowed",
            )

    tools = profile != "chat" and parsed.lane == "agent"
    max_turns = 1 if parsed.lane == "plan" else config.limits.max_turns
    return ResolvedModel(
        model_id=parsed.raw,
        lane=parsed.lane,
        profile=profile,
        workspace_alias=alias,
        workspace=workspace,
        tools=tools,
        max_turns=max_turns,
    )


def profile_policy(profile: str) -> PermissionPolicy:
    """Build the preset as an ordinary policy document.

    This is not a second permission system. It is ``default_policy_dict()``
    with decisions flipped, parsed by the same ``PermissionPolicy`` that reads
    ``agent_permissions.json``. Every rule still goes through
    ``AgentRuntime.execute``.
    """
    if profile == "operator":
        # The owner's own key follows the policy file they already maintain.
        try:
            return PermissionPolicy.load()
        except (OSError, ValueError):
            return PermissionPolicy.from_dict(default_policy_dict())

    data = default_policy_dict()
    classes = data["classes"]
    data["default"] = DENY

    if profile == "chat":
        for name in list(classes):
            classes[name] = DENY
        data["paths"]["outside_workspace"] = DENY
        data["paths"]["protected"] = DENY
        data["headless"] = {name: DENY for name in classes}
        data["headless"]["outside_workspace"] = DENY
        return PermissionPolicy.from_dict(data)

    if profile == "reader":
        for name in list(classes):
            classes[name] = DENY
        classes["read"] = ALLOW
        data["paths"]["outside_workspace"] = DENY
        data["paths"]["protected"] = DENY
        data["headless"] = {"exec": DENY, "outside_workspace": DENY}
        return PermissionPolicy.from_dict(data)

    if profile == "worker":
        classes.update(
            {
                "read": ALLOW,
                "write": ALLOW,
                "exec": DENY,
                "network": ALLOW,
                "device": DENY,
                "mcp": ALLOW,
                "native": ALLOW,
            }
        )
        data["paths"]["outside_workspace"] = DENY
        data["paths"]["protected"] = DENY
        # The alias in config/brahma_api.json is the human's trust decision.
        # ADR-020's untrusted-workspace gate would otherwise turn every API
        # write into a denial until the same repo was also trusted in the GUI.
        data["paths"]["untrusted_workspace"] = ALLOW
        data["headless"] = {"exec": DENY, "outside_workspace": DENY}
        return PermissionPolicy.from_dict(data)

    raise ValueError(f"unknown profile {profile!r}")


def policy_fingerprint(policy: PermissionPolicy) -> str:
    import hashlib
    import json

    raw = json.dumps(policy.data, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:8]


def model_rows(config: BrahmaApiConfig, principal: Principal) -> List[Dict[str, Any]]:
    """``/v1/models`` rows, filtered to what this key can actually use."""
    rows: List[Dict[str, Any]] = []
    for model_id in LISTED_MODELS:
        parsed = parse_model_id(model_id)
        if parsed is None:
            continue
        profile = _intersect(principal, parsed.profile_override)
        rows.append(
            {
                "id": model_id,
                "object": "model",
                "created": MODEL_CREATED,
                "owned_by": "brahma",
                "brahma": {
                    "lane": parsed.lane,
                    "policy": profile,
                    "tools": profile != "chat" and parsed.lane == "agent",
                    "workspace": principal.workspace if profile != "chat" else None,
                    "stream_modes": [config.stream_mode],
                },
            }
        )
    return rows
