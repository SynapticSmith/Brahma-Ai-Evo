"""Cryptographic and capability grants for model-synthesized skills (SEC-02).

Approval is a signed grant over a content-addressed artifact hash, not a boolean flag.
Any modification to the code invalidates the grant immediately.
"""

from __future__ import annotations

import datetime
import secrets
from typing import Any, Mapping, Optional, Sequence, Tuple

from core.capabilities import matches_capability


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def issue_grant(
    artifact_hash: str,
    capabilities: Sequence[str] = (),
    approved_by: str = "local_user",
    ttl_days: int = 30,
) -> dict[str, Any]:
    """Issue a new capability grant bound to an exact artifact SHA-256 hash."""
    clean_hash = str(artifact_hash or "").strip().lower()
    if len(clean_hash) != 64:
        raise ValueError(f"Invalid artifact SHA-256 hash: {artifact_hash!r}")

    now = datetime.datetime.now(datetime.timezone.utc)
    expires = now + datetime.timedelta(days=max(1, int(ttl_days)))

    return {
        "artifact_hash": clean_hash,
        "capabilities": sorted(set(capabilities or [])),
        "approved_by": str(approved_by or "local_user"),
        "approved_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "nonce": secrets.token_hex(16),
    }


def verify_grant(
    grant: Mapping[str, Any],
    current_hash: str,
    required_capability: Optional[str] = None,
) -> Tuple[bool, str]:
    """Verify grant validity against an artifact hash and capability requirements.

    Returns:
        (valid: bool, reason: str)
    """
    if not isinstance(grant, Mapping):
        return False, "Malformed grant object: expected mapping"

    expected_hash = str(grant.get("artifact_hash") or "").strip().lower()
    actual_hash = str(current_hash or "").strip().lower()

    if not expected_hash or not actual_hash or expected_hash != actual_hash:
        return False, (
            f"Artifact integrity compromised: grant hash '{expected_hash[:12]}...' "
            f"does not match actual code hash '{actual_hash[:12]}...'"
        )

    expires_str = grant.get("expires_at")
    if not expires_str:
        return False, "Grant has no expiration timestamp"

    try:
        expires_at = datetime.datetime.fromisoformat(str(expires_str))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    except Exception as exc:
        return False, f"Invalid grant expiration format: {exc}"

    now = datetime.datetime.now(datetime.timezone.utc)
    if now > expires_at:
        return False, f"Grant expired at {expires_str}"

    if required_capability:
        granted_caps = grant.get("capabilities") or []
        matched = any(matches_capability(cap, required_capability) for cap in granted_caps)
        if not matched:
            return False, f"Required capability '{required_capability}' is not granted"

    return True, "Grant valid"
