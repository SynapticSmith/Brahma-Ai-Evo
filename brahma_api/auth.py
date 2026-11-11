"""Bearer tokens, and the capability preset each one carries.

The server never needs a token's plaintext: it only verifies one. So the
registry stores ``sha256(token)`` and nothing else. That is strictly better
than encrypting a recoverable copy, because there is no key sitting beside the
ciphertext -- and no way for a later feature to "just read the token back".

A token record without a ``profile`` is rejected at load. "Forgot to choose"
must never become a capability grant, so there is no default to fall back to.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import PROFILES

#: Endpoint families a token may use. `chat` is the model surface, `models` the
#: catalogue; the rest exist so a later phase can add routes without silently
#: granting them to every key.
SCOPES = (
    "chat",
    "models",
    "diagnostics",
    "approve",
    "admin",
    "pair",
    "peer:run",
    "peer:status",
    "peer:cancel",
    "peer:artifact",
)
DEFAULT_SCOPES: Tuple[str, ...] = ("chat", "models")
RELOAD_INTERVAL_S = 1.0

_PATH_LOCKS: Dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _normalize_path(path: Path) -> str:
    s = os.path.normcase(os.path.abspath(path))
    if s.startswith("\\\\?\\"):
        s = s[4:]
    return s


def _get_path_lock(path: Path) -> threading.Lock:
    key = _normalize_path(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[key] = lock
        return lock




def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TokenRecord:
    id: str
    label: str
    sha256: str
    profile: str
    scopes: Tuple[str, ...] = DEFAULT_SCOPES
    workspace: Optional[str] = None
    provider: Optional[str] = None
    upstream_model: Optional[str] = None
    node_id: Optional[str] = None
    spki_sha256: Optional[str] = None
    created_at: str = ""
    last_used_at: Optional[str] = None
    revoked: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "sha256": self.sha256,
            "profile": self.profile,
            "scopes": list(self.scopes),
            "workspace": self.workspace,
            "provider": self.provider,
            "upstream_model": self.upstream_model,
            "node_id": self.node_id,
            "spki_sha256": self.spki_sha256,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "revoked": self.revoked,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "TokenRecord":
        """Parse one record, or raise ValueError with the reason."""
        if not isinstance(raw, dict):
            raise ValueError("token record must be an object")
        token_id = str(raw.get("id") or "").strip()
        digest = str(raw.get("sha256") or "").strip().lower()
        profile = str(raw.get("profile") or "").strip().lower()
        if not token_id:
            raise ValueError("token record has no id")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError(f"token {token_id}: sha256 must be 64 hex characters")
        if profile not in PROFILES:
            raise ValueError(
                f"token {token_id}: profile {profile!r} is missing or not one of {PROFILES}"
            )
        raw_scopes = raw.get("scopes")
        scopes: Tuple[str, ...]
        if raw_scopes is None:
            scopes = DEFAULT_SCOPES
        elif isinstance(raw_scopes, (list, tuple)):
            cleaned = tuple(str(item).strip().lower() for item in raw_scopes)
            unknown = [item for item in cleaned if item not in SCOPES]
            if unknown:
                raise ValueError(f"token {token_id}: unknown scopes {unknown}")
            scopes = cleaned
        else:
            raise ValueError(f"token {token_id}: scopes must be a list")
        workspace = raw.get("workspace")
        if workspace is not None:
            workspace = str(workspace).strip() or None
        if profile == "chat" and workspace:
            raise ValueError(f"token {token_id}: the chat preset has no workspace")
        if profile != "chat" and not workspace:
            raise ValueError(f"token {token_id}: the {profile} preset needs a workspace alias")
        return cls(
            id=token_id,
            label=str(raw.get("label") or ""),
            sha256=digest,
            profile=profile,
            scopes=scopes,
            workspace=workspace,
            provider=(str(raw["provider"]).strip() or None) if raw.get("provider") else None,
            upstream_model=(str(raw["upstream_model"]).strip() or None)
            if raw.get("upstream_model")
            else None,
            node_id=(str(raw["node_id"]).strip() or None) if raw.get("node_id") else None,
            spki_sha256=(str(raw["spki_sha256"]).strip() or None) if raw.get("spki_sha256") else None,
            created_at=str(raw.get("created_at") or ""),
            last_used_at=(str(raw["last_used_at"]) if raw.get("last_used_at") else None),
            revoked=bool(raw.get("revoked", False)),
        )


@dataclass(frozen=True)
class Principal:
    """One authenticated caller, resolved to a preset and a workspace alias."""

    token: TokenRecord

    @property
    def profile(self) -> str:
        return self.token.profile

    @property
    def workspace(self) -> Optional[str]:
        return self.token.workspace

    def has_scope(self, scope: str) -> bool:
        return scope in self.token.scopes


class TokenRegistry:
    """A hash-only token store, reloaded at most once a second.

    The reload throttle mirrors ``AgentRuntime.reload_policy``: revocation is a
    flag in the file, and a running server must notice it without a restart.
    """

    def __init__(self, path: Path, *, problems: Optional[List[str]] = None) -> None:
        self.path = Path(path)
        self.problems: List[str] = problems if problems is not None else []
        self._lock = threading.RLock()
        self._records: Dict[str, TokenRecord] = {}
        self._mtime_ns: Optional[int] = None
        self._checked_at = 0.0
        self.load()

    # -- loading -----------------------------------------------------------

    def load(self) -> None:
        """Read the file; a broken record is skipped and reported, never fatal."""
        with self._lock:
            self._records = {}
            try:
                self._mtime_ns = self.path.stat().st_mtime_ns
            except OSError:
                self._mtime_ns = None
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                self.problems.append(f"cannot read {self.path}: {exc}")
                return
            items = raw.get("tokens") if isinstance(raw, dict) else None
            if not isinstance(items, list):
                self.problems.append(f"{self.path}: expected {{'tokens': [...]}}")
                return
            seen: set[str] = set()
            for item in items:
                try:
                    record = TokenRecord.from_dict(item)
                except ValueError as exc:
                    self.problems.append(str(exc))
                    continue
                if record.id in seen:
                    self.problems.append(f"duplicate token id {record.id}; keeping the first")
                    continue
                seen.add(record.id)
                self._records[record.id] = record

    def maybe_reload(self) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._checked_at < RELOAD_INTERVAL_S:
                return
            self._checked_at = now
            try:
                mtime = self.path.stat().st_mtime_ns
            except OSError:
                return
            if mtime != self._mtime_ns:
                self.load()

    def reload_if_changed(self) -> None:
        """Reload from disk immediately if the file modified timestamp changed."""
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime_ns
            except OSError:
                return
            if mtime != self._mtime_ns:
                self.load()

    # -- queries -----------------------------------------------------------

    def records(self) -> List[TokenRecord]:
        with self._lock:
            return sorted(self._records.values(), key=lambda item: item.created_at)

    def list(self) -> List[TokenRecord]:
        """Convenience alias for :meth:`records`."""
        return self.records()

    def verify(self, token: str) -> Optional[TokenRecord]:
        """Constant-time lookup. A revoked token is indistinguishable from none."""
        if not token:
            return None
        self.maybe_reload()
        digest = hash_token(token)
        found: Optional[TokenRecord] = None
        with self._lock:
            for record in self._records.values():
                # compare_digest on equal-length hex digests; do not early-exit.
                if hmac.compare_digest(digest, record.sha256):
                    found = record
        if found is None or found.revoked:
            return None
        return found

    def touch(self, token_id: str) -> None:
        """Record use in memory only; the file is written on mint/revoke."""
        with self._lock:
            record = self._records.get(token_id)
            if record is not None:
                self._records[token_id] = replace(record, last_used_at=_now())

    # -- mutations ---------------------------------------------------------

    def mint(
        self,
        preset: str,
        *,
        label: str = "",
        workspace: Optional[str] = None,
        scopes: Optional[Iterable[str]] = None,
        provider: Optional[str] = None,
        upstream_model: Optional[str] = None,
        node_id: Optional[str] = None,
        spki_sha256: Optional[str] = None,
        confirm: Optional[str] = None,
    ) -> Tuple[TokenRecord, str]:
        """Create a key. Returns ``(record, plaintext)``; plaintext is shown once.

        ``operator`` requires ``confirm="operator"``. That is not ceremony: the
        preset is equivalent to local shell access, and a bare click must not be
        enough to hand it out.
        """
        preset = str(preset or "").strip().lower()
        if preset not in PROFILES:
            raise ValueError(f"preset must be one of {PROFILES}, not {preset!r}")
        if preset == "operator" and confirm != "operator":
            raise ValueError(
                "the operator preset is equivalent to local shell access; "
                "pass confirm='operator' to mint it"
            )
        if preset == "chat":
            if workspace:
                raise ValueError("the chat preset has no workspace")
        else:
            if not workspace:
                raise ValueError(f"the {preset} preset needs a workspace alias")
        cleaned_scopes = tuple(dict.fromkeys(str(item).strip().lower() for item in (scopes or DEFAULT_SCOPES)))
        unknown = [item for item in cleaned_scopes if item not in SCOPES]
        if unknown:
            raise ValueError(f"unknown scopes {unknown}; choose from {SCOPES}")

        plaintext = secrets.token_urlsafe(32)
        record = TokenRecord(
            id="tok_" + secrets.token_hex(4),
            label=str(label or "").strip(),
            sha256=hash_token(plaintext),
            profile=preset,
            scopes=cleaned_scopes,
            workspace=workspace,
            provider=provider,
            upstream_model=upstream_model,
            node_id=str(node_id).strip() if node_id else None,
            spki_sha256=str(spki_sha256).strip() if spki_sha256 else None,
            created_at=_now(),
        )
        with _get_path_lock(self.path):
            with self._lock:
                self.load()
                self._records[record.id] = record
                self._save()
        return record, plaintext

    def revoke(self, token_id: str) -> bool:
        with _get_path_lock(self.path):
            with self._lock:
                self.load()
                record = self._records.get(token_id)
                if record is None:
                    return False
                self._records[token_id] = replace(record, revoked=True)
                self._save()
                return True

    # -- persistence -------------------------------------------------------

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "tokens": [record.to_dict() for record in self._records.values()],
        }
        tmp = self.path.parent / f"{self.path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        try:
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            for attempt in range(15):
                try:
                    os.replace(tmp, self.path)
                    break
                except (PermissionError, OSError):
                    if attempt == 14:
                        raise
                    time.sleep(0.02 * (attempt + 1))
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        try:
            self._mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            self._mtime_ns = None
