"""Gateway Authentication & Pairing Security Manager (WS-8).

Part of Security Architecture Phase 1.
Implements:
1. Short Authentication String (SAS) 6-digit numeric pairing flow with 120s TTL.
2. Brute-force rate limiting and 10-minute lockout after 3 consecutive failures.
3. Nonce cache and replay attack protection (±60s timestamp window).
4. Command allowlist and execution policy for `/gateway/execute`.
5. Device capability and permission scope verification.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import threading
import time
from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Tuple

if TYPE_CHECKING:
    from brahma_connect.gateway.models import DeviceRecord


def constant_time_equals(left: str, right: str) -> bool:
    try:
        return hmac.compare_digest(str(left).encode("utf-8"), str(right).encode("utf-8"))
    except Exception:
        return False

logger = logging.getLogger("GatewayAuth")

# Scopes
SCOPE_DEVICE_READ = "device:read"
SCOPE_DEVICE_COMMAND = "device:command:app"
SCOPE_DEVICE_MANAGE = "device:manage"

DEFAULT_DEVICE_SCOPES: Set[str] = {SCOPE_DEVICE_READ, SCOPE_DEVICE_COMMAND}

# Pairing Configuration
PAIRING_TTL_SECONDS = 120.0       # 2 minutes
LOCKOUT_ATTEMPTS = 3              # 3 failed attempts triggers lockout
LOCKOUT_DURATION_SECONDS = 600.0  # 10 minutes lockout

# Execution Security Configuration
MAX_TIMESTAMP_SKEW_SECONDS = 60.0 # ±60 seconds
NONCE_CACHE_TTL_SECONDS = 120.0   # 2 minutes

ALLOWED_GATEWAY_COMMANDS: Set[str] = {
    "app.open",
    "device.ping",
    "system.status",
    "device.battery",
    "media.play",
    "media.pause",
    "media.next",
    "media.prev",
    "camera.snap",
    "notification.send",
    "status",
}


class SASPairingRequest:
    """Represents a pending 6-digit numeric pairing request."""

    def __init__(
        self,
        pending_id: str,
        pairing_code: str,
        device_name: str,
        platform: str,
        client_nonce: str = "",
        ip: str = "",
        ttl_seconds: float = PAIRING_TTL_SECONDS,
    ):
        self.pending_id = pending_id
        self.pairing_code = pairing_code
        self.device_name = device_name
        self.platform = platform
        self.client_nonce = client_nonce
        self.ip = ip
        self.created_at = time.time()
        self.expires_at = self.created_at + ttl_seconds

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pending_id": self.pending_id,
            "pairing_code": self.pairing_code,
            "device_name": self.device_name,
            "platform": self.platform,
            "expires_in": max(0, int(self.expires_at - time.time())),
        }


class GatewayAuthManager:
    """Manages SAS pairing, lockout protection, replay mitigation, and execution auth."""

    def __init__(self):
        self._lock = threading.RLock()
        self._pending: Dict[str, SASPairingRequest] = {}
        self._failed_attempts: Dict[str, int] = {}       # client_ip -> count
        self._lockouts: Dict[str, float] = {}            # client_ip -> locked_until
        self._seen_nonces: Dict[str, float] = {}         # nonce -> timestamp

    def _prune(self) -> None:
        now = time.time()
        # Prune expired pending requests
        expired_reqs = [pid for pid, req in self._pending.items() if req.is_expired]
        for pid in expired_reqs:
            del self._pending[pid]

        # Prune expired lockouts
        expired_locks = [ip for ip, until in self._lockouts.items() if until <= now]
        for ip in expired_locks:
            del self._lockouts[ip]
            self._failed_attempts.pop(ip, None)

        # Prune expired nonces
        expired_nonces = [
            nonce for nonce, ts in self._seen_nonces.items()
            if now - ts > NONCE_CACHE_TTL_SECONDS
        ]
        for nonce in expired_nonces:
            del self._seen_nonces[nonce]

    def is_locked_out(self, client_key: str) -> Tuple[bool, int]:
        """Checks if a client IP/identifier is locked out from pairing."""
        with self._lock:
            self._prune()
            until = self._lockouts.get(client_key)
            if until and until > time.time():
                remaining = int(until - time.time())
                return True, remaining
            return False, 0

    def create_pairing_request(
        self,
        device_name: str,
        platform: str,
        client_nonce: str = "",
        client_ip: str = "127.0.0.1",
    ) -> Tuple[bool, Dict[str, Any], str]:
        """Initiates a SAS pairing session and returns a random 6-digit code."""
        with self._lock:
            locked, remaining = self.is_locked_out(client_ip)
            if locked:
                return (
                    False,
                    {},
                    f"Pairing locked due to multiple failed attempts. Try again in {remaining}s.",
                )

            # Generate random 6-digit code
            code_int = secrets.randbelow(1_000_000)
            code = f"{code_int:06d}"
            pending_id = secrets.token_hex(16)

            req = SASPairingRequest(
                pending_id=pending_id,
                pairing_code=code,
                device_name=device_name or "Unknown Device",
                platform=platform or "unknown",
                client_nonce=client_nonce,
                ip=client_ip,
            )
            self._pending[pending_id] = req
            logger.info(f"[GatewayAuth] Created pairing request {pending_id} for '{device_name}' (code: {code})")
            return True, req.to_dict(), "Pairing request created"

    def verify_pairing_code(
        self,
        pending_id: str,
        pairing_code: str,
        client_ip: str = "127.0.0.1",
    ) -> Tuple[bool, Optional[SASPairingRequest], str]:
        """Verifies a 6-digit code submitted by a pairing client with lockout protection."""
        with self._lock:
            locked, remaining = self.is_locked_out(client_ip)
            if locked:
                return (
                    False,
                    None,
                    f"Pairing locked due to multiple failed attempts. Try again in {remaining}s.",
                )

            self._prune()
            req = self._pending.get(pending_id)
            if req is None or req.is_expired:
                return False, None, "Invalid or expired pairing request."

            # Verify code with constant-time equality
            if not constant_time_equals(req.pairing_code, str(pairing_code).strip()):
                attempts = self._failed_attempts.get(client_ip, 0) + 1
                self._failed_attempts[client_ip] = attempts
                logger.warning(
                    f"[GatewayAuth] Invalid pairing code attempt {attempts}/{LOCKOUT_ATTEMPTS} from {client_ip}"
                )
                if attempts >= LOCKOUT_ATTEMPTS:
                    until = time.time() + LOCKOUT_DURATION_SECONDS
                    self._lockouts[client_ip] = until
                    logger.warning(f"[GatewayAuth] Client {client_ip} locked out for 10 minutes.")
                    return (
                        False,
                        None,
                        f"Pairing locked out for 10 minutes due to {LOCKOUT_ATTEMPTS} consecutive failed attempts.",
                    )
                return False, None, f"Invalid pairing code. {LOCKOUT_ATTEMPTS - attempts} attempt(s) remaining."

            # Verified successfully
            self._failed_attempts.pop(client_ip, None)
            del self._pending[pending_id]
            logger.info(f"[GatewayAuth] Successfully verified pairing code for {req.device_name}")
            return True, req, "Pairing code verified."

    def get_pending_request(self, pending_id: str) -> Optional[SASPairingRequest]:
        with self._lock:
            self._prune()
            return self._pending.get(pending_id)

    def cancel_pending_request(self, pending_id: str) -> bool:
        with self._lock:
            return bool(self._pending.pop(pending_id, None))

    def has_scope(self, device: DeviceRecord, required_scope: str) -> bool:
        """Verifies that a device record possesses the requested capability scope."""
        if not device or device.revoked:
            return False
        # Manage scope requires explicit declaration
        if required_scope == SCOPE_DEVICE_MANAGE:
            return SCOPE_DEVICE_MANAGE in device.permissions
        # Normal command and read scopes are granted by default to all valid paired devices
        return (
            required_scope in DEFAULT_DEVICE_SCOPES
            or required_scope in device.permissions
            or required_scope in device.capabilities
        )

    def validate_execution_request(
        self,
        command: str,
        args: Any,
        nonce: str,
        ts: float,
    ) -> Tuple[bool, str]:
        """Validates command schema, allowlist, timestamp skew, and nonce replay."""
        now = time.time()

        # 1. Command Allowlist
        cmd = str(command or "").strip()
        if cmd not in ALLOWED_GATEWAY_COMMANDS:
            return False, f"Command '{cmd}' is not allowed via the gateway execute API."

        # 2. Arguments format
        if not isinstance(args, dict):
            return False, "Command arguments must be a JSON object."

        # 3. Timestamp skew check (±60s)
        try:
            ts_float = float(ts)
        except (ValueError, TypeError):
            return False, "Invalid or missing timestamp."

        skew = abs(now - ts_float)
        if skew > MAX_TIMESTAMP_SKEW_SECONDS:
            return False, f"Timestamp skewed outside allowed window ({skew:.1f}s > {MAX_TIMESTAMP_SKEW_SECONDS}s)."

        # 4. Nonce Replay Protection
        nonce_str = str(nonce or "").strip()
        if not nonce_str or len(nonce_str) < 8:
            return False, "Missing or insufficient nonce (must be at least 8 characters)."

        with self._lock:
            self._prune()
            if nonce_str in self._seen_nonces:
                logger.warning(f"[GatewayAuth] Replay attack detected: duplicate nonce '{nonce_str}'")
                return False, "Replay attack detected: duplicate nonce."
            self._seen_nonces[nonce_str] = now

        return True, "Valid"
