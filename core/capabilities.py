"""
Canonical Capability Definitions, Scoping & Matching Engine (SEC-16, WS-2, WS-5).

Centralizes system-wide capability constants, parsing, and matching semantics for:
1. Model-synthesized skill grants (`core.grants`).
2. AppContainer profile permissions (`sandbox.launcher_win`).
3. Tool execution policies (`core.tool_policy`).
4. Gateway remote control authorizations (`core.gateway_auth`).
5. Secret broker capability scoping (`core.secret_broker`).
"""

from __future__ import annotations

import fnmatch
from typing import Dict, List, Optional, Sequence, Set, Tuple

# Base Capability Names
CAP_FS_READ = "fs.read"
CAP_FS_WRITE = "fs.write"
CAP_NET_CLIENT = "net.client"
CAP_NET_SERVER = "net.server"
CAP_PROCESS_SPAWN = "process.spawn"
CAP_PROCESS_EVAL = "process.eval"
CAP_SECRET_READ = "secret.read"
CAP_SECRET_WRITE = "secret.write"
CAP_AUDIO_LISTEN = "audio.listen"
CAP_AUDIO_SPEAK = "audio.speak"
CAP_ADMIN = "admin"

ALL_CANONICAL_CAPABILITIES: Set[str] = {
    CAP_FS_READ,
    CAP_FS_WRITE,
    CAP_NET_CLIENT,
    CAP_NET_SERVER,
    CAP_PROCESS_SPAWN,
    CAP_PROCESS_EVAL,
    CAP_SECRET_READ,
    CAP_SECRET_WRITE,
    CAP_AUDIO_LISTEN,
    CAP_AUDIO_SPEAK,
    CAP_ADMIN,
}

# Descriptions for UI review dialogs and audit explanations
CAPABILITY_DESCRIPTIONS: Dict[str, str] = {
    CAP_FS_READ: "Read access to workspace or specified file system paths",
    CAP_FS_WRITE: "Write/modify access to workspace or specified file system paths",
    CAP_NET_CLIENT: "Outbound network requests (HTTP, TCP)",
    CAP_NET_SERVER: "Inbound network listening and binding",
    CAP_PROCESS_SPAWN: "Spawning external subprocesses or shell commands",
    CAP_PROCESS_EVAL: "Executing isolated dynamic Python evaluation",
    CAP_SECRET_READ: "Accessing sensitive stored credentials or API keys",
    CAP_SECRET_WRITE: "Modifying stored credentials or API keys",
    CAP_AUDIO_LISTEN: "Access to microphone / audio input stream",
    CAP_AUDIO_SPEAK: "Access to audio output / text-to-speech synthesize",
    CAP_ADMIN: "Full unrestricted administrative access",
}


def parse_capability(cap_str: str) -> Tuple[str, Optional[str]]:
    """
    Parses a capability string into (base_cap, target_spec).

    Examples:
        "fs.read:docs/*" -> ("fs.read", "docs/*")
        "net.client:api.google.com" -> ("net.client", "api.google.com")
        "process.spawn" -> ("process.spawn", None)
    """
    cleaned = cap_str.strip()
    if ":" in cleaned:
        base, target = cleaned.split(":", 1)
        return base.strip(), target.strip()
    return cleaned, None


def matches_capability(granted: str, requested: str) -> bool:
    """
    Evaluates whether a granted capability satisfies a requested capability requirement.

    Supports:
    - Global wildcard: "*" matches any request.
    - Exact match: "fs.read" matches "fs.read".
    - Base wildcard: "fs.*" matches "fs.read" and "fs.write".
    - Target globbing: "fs.read:docs/*" matches "fs.read:docs/file.txt".
    - Scope wildcard: "fs.read:*" matches any "fs.read:<target>".
    """
    g = granted.strip()
    r = requested.strip()

    if g == "*" or g == r:
        return True

    g_base, g_target = parse_capability(g)
    r_base, r_target = parse_capability(r)

    # Base match check
    base_matched = False
    if g_base == r_base:
        base_matched = True
    elif g_base.endswith(".*") and r_base.startswith(g_base[:-2]):
        base_matched = True

    if not base_matched:
        return False

    # If granted has no target restriction, it grants full base capability
    if g_target is None or g_target == "*":
        return True

    # If requested has no target, but granted is restricted to a target, deny
    if r_target is None:
        return False

    # Glob match target
    return fnmatch.fnmatch(r_target, g_target) or r_target == g_target


def evaluate_capabilities(
    granted_capabilities: Sequence[str],
    required_capabilities: Sequence[str],
) -> Tuple[bool, List[str]]:
    """
    Checks if all required capabilities are satisfied by granted capabilities.

    Returns:
        (True, []) if all satisfied.
        (False, missing_caps) if one or more are unsatisfied.
    """
    missing: List[str] = []
    for req in required_capabilities:
        satisfied = any(matches_capability(g, req) for g in granted_capabilities)
        if not satisfied:
            missing.append(req)

    return len(missing) == 0, missing
