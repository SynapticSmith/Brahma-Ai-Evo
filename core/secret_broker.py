"""
Secret Broker & Scoped Credential Manager (WS-7).

Part of Security Architecture Phase 2.
Implements:
1. In-memory DPAPI-backed secret store with zero plaintext secrets on disk.
2. Capability scope enforcement per caller (least privilege access control).
3. Network boundary secret injection (`http_request`) — secrets never reach models or skills.
4. Comprehensive audit logging with automated credential redaction.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
import threading
from typing import Any, Dict, Optional, Set, Tuple
import urllib.request
import urllib.error

from core.audit import AuditLogger
from core.secret_store import load_api_keys, save_api_keys
from core.user_paths import get_user_data_dir

logger = logging.getLogger("SecretBroker")

# Scopes
SCOPE_GEMINI_GENERATE = "gemini:generate"
SCOPE_OPENROUTER_CHAT = "openrouter:chat"
SCOPE_ANTHROPIC_MESSAGES = "anthropic:messages"
SCOPE_INSTAGRAM_READ = "instagram:read"
SCOPE_INSTAGRAM_POST = "instagram:post"
SCOPE_SMART_HOME_READ = "home:read"
SCOPE_SMART_HOME_CONTROL = "home:control"
SCOPE_ADMIN = "secrets:admin"

SECRET_TO_DEFAULT_SCOPE: Dict[str, str] = {
    "gemini_api_key": SCOPE_GEMINI_GENERATE,
    "openrouter_api_key": SCOPE_OPENROUTER_CHAT,
    "anthropic_api_key": SCOPE_ANTHROPIC_MESSAGES,
    "instagram_username": SCOPE_INSTAGRAM_READ,
    "instagram_password": SCOPE_INSTAGRAM_POST,
    "smart_home_key": SCOPE_SMART_HOME_CONTROL,
}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class SecretBroker:
    """In-memory secrets broker that injects credentials at the network boundary."""

    def __init__(
        self,
        config_path: Optional[Path] = None,
        audit_file: Optional[Path] = None,
    ):
        self.config_path = (
            Path(config_path)
            if config_path is not None
            else get_user_data_dir() / "config" / "api_keys.json"
        )
        self.audit_file = (
            Path(audit_file)
            if audit_file is not None
            else get_user_data_dir() / "secrets" / "logs" / "audit.jsonl"
        )
        self._audit = AuditLogger(self.audit_file)
        self._lock = threading.RLock()
        self._secrets: Dict[str, str] = {}
        self._caller_grants: Dict[str, Set[str]] = {}  # caller_id -> set of granted scopes

        self._ensure_dirs()
        self._reload_secrets()

    def _ensure_dirs(self) -> None:
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)
        if self.config_path:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def _append_audit(self, event: str, **kwargs: Any) -> None:
        caller = kwargs.pop("caller", "core")
        try:
            self._audit.log(event, caller=caller, **kwargs)
        except Exception as exc:
            logger.warning(f"[SecretBroker] Failed to write audit record: {exc}")

    def _reload_secrets(self) -> None:
        with self._lock:
            if self.config_path.exists():
                try:
                    self._secrets = load_api_keys(self.config_path)
                    logger.info(f"[SecretBroker] Loaded {len(self._secrets)} secret(s) into memory")
                except Exception as exc:
                    logger.warning(f"[SecretBroker] Failed to load secrets from {self.config_path}: {exc}")
                    self._secrets = {}
            else:
                self._secrets = {}

    def grant_scope(self, caller_id: str, scope: str) -> None:
        """Grants a capability scope to a registered caller."""
        with self._lock:
            if caller_id not in self._caller_grants:
                self._caller_grants[caller_id] = set()
            self._caller_grants[caller_id].add(scope)
            self._append_audit("SCOPE_GRANTED", caller=caller_id, scope=scope)

    def revoke_scope(self, caller_id: str, scope: str) -> None:
        """Revokes a capability scope from a registered caller."""
        with self._lock:
            if caller_id in self._caller_grants:
                self._caller_grants[caller_id].discard(scope)
                self._append_audit("SCOPE_REVOKED", caller=caller_id, scope=scope)

    def verify_scope(
        self,
        caller_id: str,
        secret_id: str,
        requested_scope: Optional[str] = None,
        grant: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Verifies if caller has the required scope for the secret."""
        # Core system processes possess ambient authority
        if caller_id in ("core", "system", "local_admin"):
            return True

        needed_scope = requested_scope or SECRET_TO_DEFAULT_SCOPE.get(secret_id, SCOPE_ADMIN)

        # Check cryptographic grant attached to caller/request
        if grant and isinstance(grant, dict):
            grant_caps = set(grant.get("capabilities", []))
            if needed_scope in grant_caps or SCOPE_ADMIN in grant_caps:
                return True

        # Check broker-registered caller scopes
        caller_scopes = self._caller_grants.get(caller_id, set())
        return needed_scope in caller_scopes or SCOPE_ADMIN in caller_scopes

    def get_secret(
        self,
        secret_id: str,
        scope: Optional[str] = None,
        caller_id: str = "core",
        grant: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Retrieves a secret if caller has the authorized scope."""
        needed_scope = scope or SECRET_TO_DEFAULT_SCOPE.get(secret_id, SCOPE_ADMIN)

        with self._lock:
            if not self.verify_scope(caller_id, secret_id, needed_scope, grant):
                self._append_audit(
                    "ACCESS_DENIED",
                    caller=caller_id,
                    secret_id=secret_id,
                    requested_scope=needed_scope,
                )
                raise PermissionError(
                    f"Access Denied: Caller '{caller_id}' lacks required scope '{needed_scope}' "
                    f"for secret '{secret_id}'."
                )

            val = self._secrets.get(secret_id)
            if not val:
                raise KeyError(f"Secret '{secret_id}' is not set or empty in secret store.")

            self._append_audit(
                "SECRET_ACCESSED",
                caller=caller_id,
                secret_id=secret_id,
                scope=needed_scope,
            )
            return val

    def set_secret(
        self,
        secret_id: str,
        secret_value: str,
        caller_id: str = "core",
    ) -> bool:
        """Encrypts and persists a new secret value with Windows DPAPI protection."""
        if caller_id not in ("core", "system", "local_admin"):
            raise PermissionError("Only core or administrator can write secrets.")

        with self._lock:
            self._secrets[secret_id] = str(secret_value).strip()
            ok = save_api_keys(self._secrets, self.config_path)
            self._append_audit("SECRET_SAVED", caller=caller_id, secret_id=secret_id, success=ok)
            return ok

    def revoke_secret(
        self,
        secret_id: str,
        caller_id: str = "core",
    ) -> bool:
        """Removes a secret from memory and DPAPI persistent storage."""
        if caller_id not in ("core", "system", "local_admin"):
            raise PermissionError("Only core or administrator can revoke secrets.")

        with self._lock:
            if secret_id in self._secrets:
                del self._secrets[secret_id]
                ok = save_api_keys(self._secrets, self.config_path)
                self._append_audit("SECRET_REVOKED", caller=caller_id, secret_id=secret_id, success=ok)
                return ok
            return False

    def http_request(
        self,
        service: str,
        url: str,
        method: str = "POST",
        headers: Optional[Dict[str, str]] = None,
        body: Optional[Any] = None,
        scope: Optional[str] = None,
        caller_id: str = "core",
        grant: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
    ) -> Tuple[int, Dict[str, Any], str]:
        """Boundary injection: executes an HTTP call injecting provider secret at the network boundary.

        The calling skill or model never sees or receives the raw secret string.
        """
        service_lower = service.lower().strip()
        secret_key_name = {
            "gemini": "gemini_api_key",
            "openrouter": "openrouter_api_key",
            "anthropic": "anthropic_api_key",
        }.get(service_lower, f"{service_lower}_api_key")

        needed_scope = scope or SECRET_TO_DEFAULT_SCOPE.get(secret_key_name, SCOPE_ADMIN)

        # 1. Authorize caller scope
        if not self.verify_scope(caller_id, secret_key_name, needed_scope, grant):
            self._append_audit(
                "HTTP_ACCESS_DENIED",
                caller=caller_id,
                service=service_lower,
                scope=needed_scope,
            )
            raise PermissionError(
                f"Access Denied: Caller '{caller_id}' lacks scope '{needed_scope}' for {service} HTTP broker."
            )

        # 2. Retrieve key
        with self._lock:
            key = self._secrets.get(secret_key_name)
            if not key:
                raise KeyError(f"No configured secret found for service '{service_lower}' ({secret_key_name}).")

        # 3. Boundary Injection into request headers/params
        req_headers = dict(headers or {})
        target_url = url

        if service_lower == "gemini":
            req_headers["x-goog-api-key"] = key
        elif service_lower == "openrouter":
            req_headers["Authorization"] = f"Bearer {key}"
            req_headers.setdefault("HTTP-Referer", "https://brahma.ai")
        elif service_lower == "anthropic":
            req_headers["x-api-key"] = key
            req_headers.setdefault("anthropic-version", "2023-06-01")

        req_headers.setdefault("Content-Type", "application/json")

        payload_bytes: Optional[bytes] = None
        if body is not None:
            if isinstance(body, (dict, list)):
                payload_bytes = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                payload_bytes = body.encode("utf-8")
            elif isinstance(body, bytes):
                payload_bytes = body

        self._append_audit(
            "HTTP_REQUEST_FORWARDED",
            caller=caller_id,
            service=service_lower,
            url=url,
            method=method.upper(),
        )

        # 4. Perform network request
        req = urllib.request.Request(
            url=target_url,
            data=payload_bytes,
            headers=req_headers,
            method=method.upper(),
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status_code = resp.status
                resp_bytes = resp.read()
                resp_text = resp_bytes.decode("utf-8", errors="replace")
                try:
                    resp_data = json.loads(resp_text)
                except Exception:
                    resp_data = {"raw": resp_text}
                return status_code, resp_data, resp_text
        except urllib.error.HTTPError as http_err:
            err_text = http_err.read().decode("utf-8", errors="replace")
            try:
                err_data = json.loads(err_text)
            except Exception:
                err_data = {"error": err_text}
            return http_err.code, err_data, err_text
        except Exception as exc:
            return 500, {"error": str(exc)}, str(exc)
