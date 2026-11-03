"""
Taint-Aware Policy & Single-Use Action Gate (WS-9).

Part of Security Architecture Phase 2.
Implements:
1. Classification of tools into read-only vs state-changing operations.
2. Tainted context policy enforcement:
   - Read-only tools proceed automatically.
   - State-changing tools require explicit human approval (ASK) or are DENIED.
3. Cryptographically bound, single-use human approval tokens with 60-second TTL.
   - Reusing approvals for different commands, parameters, or after expiry fails.
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import secrets
import time
from typing import Any, Dict, Optional, Set, Tuple

logger = logging.getLogger("TaintPolicy")


class Decision(enum.Enum):
    ALLOW = "ALLOW"
    ASK = "ASK"
    DENY = "DENY"


READ_ONLY_PREFIXES = (
    "read_",
    "get_",
    "list_",
    "search_",
    "view_",
    "check_",
    "show_",
)

READ_ONLY_TOOLS: Set[str] = {
    "read_file",
    "list_files",
    "search_web",
    "system_info",
    "recall_memory",
    "get_weather",
    "get_calendar",
    "get_status",
}

STATE_CHANGING_TOOLS: Set[str] = {
    "write_file",
    "edit_file",
    "delete_file",
    "run_command",
    "bash",
    "python_repl",
    "skill_forge",
    "send_message",
    "instagram_post",
    "instagram_comment",
    "smart_home_control",
    "install_plugin",
}


def canonical_json_str(data: Dict[str, Any]) -> str:
    """Produces sorted, compact canonical JSON for consistent action hashing."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def compute_action_digest(tool_name: str, args: Dict[str, Any]) -> str:
    """Computes a SHA-256 digest binding tool name and exact arguments."""
    payload = f"{tool_name.lower().strip()}:{canonical_json_str(args)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TaintPolicy:
    """Evaluates security decisions for tool invocations based on taint status and approvals."""

    def __init__(self, approval_ttl_seconds: float = 60.0):
        self.approval_ttl_seconds = float(approval_ttl_seconds)
        self._consumed_nonces: Set[str] = set()

    def is_read_only(self, tool_name: str) -> bool:
        """Determines whether a tool is inherently read-only."""
        name_lower = tool_name.lower().strip()
        if name_lower in READ_ONLY_TOOLS:
            return True
        if name_lower in STATE_CHANGING_TOOLS:
            return False
        return any(name_lower.startswith(prefix) for prefix in READ_ONLY_PREFIXES)

    def create_approval(
        self,
        tool_name: str,
        args: Dict[str, Any],
        approved_by: str = "user",
    ) -> Dict[str, Any]:
        """Creates a single-use, time-bound approval token for an exact tool call."""
        action_digest = compute_action_digest(tool_name, args)
        nonce = secrets.token_hex(16)
        now = time.time()

        return {
            "action_digest": action_digest,
            "tool_name": tool_name,
            "nonce": nonce,
            "approved_by": approved_by,
            "created_at": now,
            "expires_at": now + self.approval_ttl_seconds,
        }

    def verify_approval(
        self,
        approval: Dict[str, Any],
        tool_name: str,
        args: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """Validates and consumes an approval token, enforcing exact match, TTL, and single-use."""
        if not isinstance(approval, dict):
            return False, "Malformed approval token."

        nonce = approval.get("nonce")
        if not nonce or nonce in self._consumed_nonces:
            return False, "Approval token has already been consumed or is missing nonce (replay rejected)."

        now = time.time()
        expires_at = approval.get("expires_at", 0)
        if now > expires_at:
            return False, "Approval token has expired (60s TTL exceeded)."

        expected_digest = compute_action_digest(tool_name, args)
        actual_digest = approval.get("action_digest")
        if actual_digest != expected_digest:
            return False, (
                "Approval digest mismatch: approval was granted for a different action/target."
            )

        # Mark consumed
        self._consumed_nonces.add(nonce)
        return True, "Approval valid and verified."

    def decide(
        self,
        tool_name: str,
        args: Dict[str, Any],
        is_tainted: bool = False,
        approval: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Decision, str]:
        """Makes a policy decision: ALLOW, ASK, or DENY."""
        tool_clean = tool_name.strip()

        # 1. Untainted execution allows normal flow
        if not is_tainted:
            return Decision.ALLOW, "Execution allowed in untainted context."

        # 2. Tainted context: Read-only operations are safe to auto-allow
        if self.is_read_only(tool_clean):
            return Decision.ALLOW, f"Read-only tool '{tool_clean}' permitted under taint."

        # 3. Tainted context: State-changing tools require valid human approval
        if approval is not None:
            ok, reason = self.verify_approval(approval, tool_clean, args)
            if ok:
                return Decision.ALLOW, f"State-changing tool '{tool_clean}' approved by human."
            return Decision.DENY, f"Invalid or expired approval for '{tool_clean}': {reason}"

        return (
            Decision.ASK,
            f"Tainted context attempting state-changing tool '{tool_clean}': user confirmation required.",
        )
