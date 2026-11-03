"""
Centralized Tamper-Evident Audit Logging Engine (SEC-16, Q4 Assurance).

Provides append-only, cryptographic hash-chained audit logging for all security-sensitive
events across Brahma AI (secrets, tokens, skill grants, sandbox escapes, gateway auth,
taint violations, and release verification).

Key Properties:
1. Append-only JSONL format.
2. Monotonic sequence numbering.
3. Cryptographic hash-chaining: record_i hash includes prev_hash from record_{i-1}.
4. Automated redaction: all details pass through `core.redaction.redact_data`.
5. Fast offline chain verification (`verify_audit_log`).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from pathlib import Path
import threading
from typing import Any, Dict, Optional, Tuple

from core.redaction import redact_data
from core.user_paths import get_user_data_dir

logger = logging.getLogger("brahma.audit")

GENESIS_HASH = "0" * 64


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def canonical_json(data: Any) -> str:
    """Serializes data to canonical JSON with sorted keys and compact separators."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_record_hash(
    sequence: int,
    timestamp: str,
    event: str,
    caller: str,
    details: Dict[str, Any],
    prev_hash: str,
) -> str:
    """Computes a SHA-256 digest over the canonical representation of an audit entry."""
    payload = (
        f"{sequence}|"
        f"{timestamp}|"
        f"{event.strip().upper()}|"
        f"{caller.strip()}|"
        f"{canonical_json(details)}|"
        f"{prev_hash.strip().lower()}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditLogger:
    """Thread-safe, hash-chained audit logger writing to an append-only JSONL file."""

    def __init__(self, log_path: Optional[Path] = None) -> None:
        self.log_path = (
            Path(log_path).resolve()
            if log_path is not None
            else get_user_data_dir() / "logs" / "audit.jsonl"
        )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._last_sequence = -1
        self._last_hash = GENESIS_HASH
        self._init_chain()

    def _init_chain(self) -> None:
        """Inspects existing log file to synchronize sequence and last record hash."""
        with self._lock:
            if not self.log_path.exists() or self.log_path.stat().st_size == 0:
                self._last_sequence = -1
                self._last_hash = GENESIS_HASH
                return

            last_line = ""
            try:
                with open(self.log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            last_line = line
                if last_line:
                    rec = json.loads(last_line)
                    self._last_sequence = int(rec.get("sequence", 0))
                    self._last_hash = str(rec.get("record_hash", GENESIS_HASH)).lower()
            except Exception as exc:
                logger.warning(f"[AuditLogger] Could not resume audit chain from existing file: {exc}")
                # Fallback to fresh sequence start if corrupt
                self._last_sequence = -1
                self._last_hash = GENESIS_HASH

    def log(
        self,
        event: str,
        caller: str = "core",
        **details: Any,
    ) -> Dict[str, Any]:
        """Appends a cryptographically verified and redacted audit entry to the log."""
        with self._lock:
            sequence = self._last_sequence + 1
            timestamp = _now_iso()
            event_clean = str(event).strip().upper()
            caller_clean = str(caller).strip()

            # Ensure all sensitive data (credentials, keys, tokens) are scrubbed
            cleaned_details = redact_data(details) if details else {}

            record_hash = compute_record_hash(
                sequence=sequence,
                timestamp=timestamp,
                event=event_clean,
                caller=caller_clean,
                details=cleaned_details,
                prev_hash=self._last_hash,
            )

            record: Dict[str, Any] = {
                "sequence": sequence,
                "timestamp": timestamp,
                "event": event_clean,
                "caller": caller_clean,
                "details": cleaned_details,
                "prev_hash": self._last_hash,
                "record_hash": record_hash,
            }

            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(canonical_json(record) + "\n")

                self._last_sequence = sequence
                self._last_hash = record_hash
            except Exception as exc:
                logger.error(f"[AuditLogger] Failed to write audit log entry: {exc}")

            return record

    def verify(self) -> Tuple[bool, str]:
        """Verifies integrity of the active audit log file."""
        with self._lock:
            return verify_audit_log(self.log_path)


def verify_audit_log(path: Path) -> Tuple[bool, str]:
    """
    Validates cryptographic integrity and hash chain continuity of an audit log.

    Returns:
        (True, f"Verified {count} records successfully") if chain is unbroken.
        (False, error_reason) if any tampering, omission, or corruption is detected.
    """
    file_path = Path(path).resolve()
    if not file_path.exists():
        return True, "Audit log does not exist yet (empty chain is valid)."

    expected_sequence = 0
    expected_prev_hash = GENESIS_HASH
    record_count = 0

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line_no, raw_line in enumerate(f, start=1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue

                try:
                    record = json.loads(raw_line)
                except Exception as err:
                    return False, f"Line {line_no}: Malformed JSON record ({err})"

                seq = record.get("sequence")
                timestamp = record.get("timestamp")
                event = record.get("event")
                caller = record.get("caller")
                details = record.get("details", {})
                prev_hash = record.get("prev_hash")
                claimed_hash = record.get("record_hash")

                if seq != expected_sequence:
                    return (
                        False,
                        f"Line {line_no}: Sequence mismatch. Expected {expected_sequence}, got {seq}",
                    )

                if prev_hash != expected_prev_hash:
                    return (
                        False,
                        f"Line {line_no}: Chain broken at sequence {seq}. "
                        f"Expected prev_hash '{expected_prev_hash}', got '{prev_hash}'",
                    )

                recomputed_hash = compute_record_hash(
                    sequence=seq,
                    timestamp=timestamp,
                    event=event,
                    caller=caller,
                    details=details,
                    prev_hash=prev_hash,
                )

                if recomputed_hash != claimed_hash:
                    return (
                        False,
                        f"Line {line_no}: Tampering detected! Recomputed hash '{recomputed_hash}' "
                        f"does not match record_hash '{claimed_hash}'",
                    )

                expected_prev_hash = claimed_hash
                expected_sequence += 1
                record_count += 1

    except Exception as exc:
        return False, f"Failed to read audit log: {exc}"

    return True, f"Verified {record_count} record(s) in audit chain successfully."


# Global singleton management
_DEFAULT_LOGGER: Optional[AuditLogger] = None
_LOGGER_LOCK = threading.Lock()


def get_audit_logger(log_path: Optional[Path] = None) -> AuditLogger:
    """Returns the central AuditLogger instance, creating it if needed."""
    global _DEFAULT_LOGGER
    if log_path is not None:
        return AuditLogger(log_path)

    with _LOGGER_LOCK:
        if _DEFAULT_LOGGER is None:
            _DEFAULT_LOGGER = AuditLogger()
        return _DEFAULT_LOGGER


def record_audit_event(event: str, caller: str = "core", **details: Any) -> Dict[str, Any]:
    """Convenience helper to log a security event to the default audit log."""
    return get_audit_logger().log(event, caller=caller, **details)
