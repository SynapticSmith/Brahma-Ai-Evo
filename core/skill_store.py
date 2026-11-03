"""Content-addressed skill store and approval state machine (SEC-01).

Implements:
1. Content-addressed storage under `%LOCALAPPDATA%\\BrahmaAI\\skills\\store\\<sha256>\\`.
2. Lifecycle state machine: draft -> approved -> installed | rejected | quarantine.
3. Cryptographic grant binding via `core.grants`.
4. Append-only audit logging to `%LOCALAPPDATA%\\BrahmaAI\\skills\\logs\\audit.jsonl`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import datetime
import hashlib
import json
import logging
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.audit import AuditLogger
from core.grants import issue_grant, verify_grant
from core.user_paths import atomic_write_json, get_user_data_dir

logger = logging.getLogger("SkillStore")

# Skill Lifecycle States
STATE_DRAFT = "draft"
STATE_APPROVED = "approved"
STATE_INSTALLED = "installed"
STATE_REJECTED = "rejected"
STATE_QUARANTINE = "quarantine"

VALID_STATES = {STATE_DRAFT, STATE_APPROVED, STATE_INSTALLED, STATE_REJECTED, STATE_QUARANTINE}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def compute_artifact_hash(
    name: str,
    code: str,
    manifest: Dict[str, Any],
    test_cases: List[Dict[str, Any]],
) -> str:
    """Computes deterministic SHA-256 hash over canonical skill artifact payload."""
    payload = {
        "name": str(name).strip(),
        "manifest": manifest,
        "code": str(code),
        "test_cases": test_cases,
    }
    canonical_repr = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()


@dataclass
class SkillArtifact:
    artifact_hash: str
    name: str
    state: str
    code: str
    manifest: Dict[str, Any]
    test_cases: List[Dict[str, Any]]
    created_at: str = field(default_factory=_now_iso)
    approved_at: Optional[str] = None
    approved_by: Optional[str] = None
    grant: Optional[Dict[str, Any]] = None
    telemetry: Optional[Dict[str, Any]] = None
    rejection_reason: Optional[str] = None

    def to_index_record(self) -> Dict[str, Any]:
        return {
            "artifact_hash": self.artifact_hash,
            "name": self.name,
            "state": self.state,
            "created_at": self.created_at,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "grant": self.grant,
            "rejection_reason": self.rejection_reason,
        }


class SkillStore:
    """Content-addressed skill artifact repository."""

    def __init__(self, root_dir: Optional[Path] = None):
        self.root_dir = Path(root_dir) if root_dir is not None else get_user_data_dir() / "skills"
        self.store_dir = self.root_dir / "store"
        self.index_file = self.root_dir / "index.json"
        self.audit_file = self.root_dir / "logs" / "audit.jsonl"
        self._audit = AuditLogger(self.audit_file)
        self._lock = threading.Lock()

        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)

    def _append_audit(self, event: str, **details: Any) -> None:
        caller = details.pop("caller", "skill_store")
        try:
            self._audit.log(event, caller=caller, **details)
        except Exception as exc:
            logger.warning(f"[SkillStore] Failed to write audit log: {exc}")

    def _load_index(self) -> Dict[str, Any]:
        if not self.index_file.exists():
            return {}
        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_index(self, index: Dict[str, Any]) -> None:
        atomic_write_json(self.index_file, index)

    def put_draft(
        self,
        name: str,
        code: str,
        manifest: Dict[str, Any],
        test_cases: Optional[List[Dict[str, Any]]] = None,
        telemetry: Optional[Dict[str, Any]] = None,
    ) -> SkillArtifact:
        """Stores a freshly synthesized skill as a quarantined draft."""
        tests = test_cases or [{"input": {}}]
        artifact_hash = compute_artifact_hash(name, code, manifest, tests)

        artifact_dir = self.store_dir / artifact_hash
        artifact_dir.mkdir(parents=True, exist_ok=True)

        # Write immutable artifact parts
        (artifact_dir / "code.py").write_text(code, encoding="utf-8")
        atomic_write_json(artifact_dir / "manifest.json", manifest)
        atomic_write_json(artifact_dir / "tests.json", tests)

        artifact = SkillArtifact(
            artifact_hash=artifact_hash,
            name=name,
            state=STATE_DRAFT,
            code=code,
            manifest=manifest,
            test_cases=tests,
            telemetry=telemetry,
        )

        meta = asdict(artifact)
        meta.pop("code", None)  # Code is in code.py
        atomic_write_json(artifact_dir / "artifact.json", meta)

        with self._lock:
            index = self._load_index()
            index[name] = artifact.to_index_record()
            self._save_index(index)

        self._append_audit("DRAFT_STAGED", name=name, hash=artifact_hash)
        logger.info(f"[SkillStore] Staged draft '{name}' ({artifact_hash[:12]})")
        return artifact

    def get_artifact(self, artifact_hash: str) -> Optional[SkillArtifact]:
        """Loads a skill artifact by SHA-256 hash."""
        clean_hash = str(artifact_hash or "").strip().lower()
        artifact_dir = self.store_dir / clean_hash
        if not artifact_dir.exists():
            return None

        code_file = artifact_dir / "code.py"
        manifest_file = artifact_dir / "manifest.json"
        tests_file = artifact_dir / "tests.json"
        meta_file = artifact_dir / "artifact.json"

        if not (code_file.exists() and manifest_file.exists() and meta_file.exists()):
            return None

        try:
            code = code_file.read_text(encoding="utf-8")
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            tests = []
            if tests_file.exists():
                with open(tests_file, "r", encoding="utf-8") as f:
                    tests = json.load(f)
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)

            return SkillArtifact(
                artifact_hash=clean_hash,
                name=meta.get("name", ""),
                state=meta.get("state", STATE_DRAFT),
                code=code,
                manifest=manifest,
                test_cases=tests,
                created_at=meta.get("created_at", _now_iso()),
                approved_at=meta.get("approved_at"),
                approved_by=meta.get("approved_by"),
                grant=meta.get("grant"),
                telemetry=meta.get("telemetry"),
                rejection_reason=meta.get("rejection_reason"),
            )
        except Exception as exc:
            logger.warning(f"[SkillStore] Failed to read artifact {clean_hash}: {exc}")
            return None

    def get_artifact_by_name(self, name: str) -> Optional[SkillArtifact]:
        """Looks up the most current artifact record registered under skill name."""
        with self._lock:
            index = self._load_index()
            record = index.get(name)
            if not record:
                return None
            return self.get_artifact(record["artifact_hash"])

    def list_artifacts(self, state: Optional[str] = None) -> List[SkillArtifact]:
        """Lists all artifacts, optionally filtered by state."""
        artifacts: List[SkillArtifact] = []
        with self._lock:
            index = self._load_index()
            for record in index.values():
                if state is None or record.get("state") == state:
                    art = self.get_artifact(record.get("artifact_hash", ""))
                    if art:
                        artifacts.append(art)
        return artifacts

    def set_state(
        self,
        artifact_hash: str,
        new_state: str,
        reason: str = "",
        grant: Optional[Dict[str, Any]] = None,
        approved_by: Optional[str] = None,
    ) -> bool:
        """Transitions an artifact's state."""
        if new_state not in VALID_STATES:
            raise ValueError(f"Invalid state: {new_state}")

        artifact = self.get_artifact(artifact_hash)
        if not artifact:
            return False

        old_state = artifact.state
        artifact.state = new_state
        if new_state == STATE_APPROVED:
            artifact.approved_at = _now_iso()
            artifact.approved_by = approved_by or "local_user"
            artifact.grant = grant
        elif new_state == STATE_REJECTED:
            artifact.rejection_reason = reason

        # Persist updated meta
        meta_file = self.store_dir / artifact.artifact_hash / "artifact.json"
        meta = asdict(artifact)
        meta.pop("code", None)
        atomic_write_json(meta_file, meta)

        with self._lock:
            index = self._load_index()
            index[artifact.name] = artifact.to_index_record()
            self._save_index(index)

        self._append_audit(
            "STATE_TRANSITION",
            hash=artifact.artifact_hash,
            name=artifact.name,
            from_state=old_state,
            to_state=new_state,
            reason=reason,
        )
        return True

    def approve_artifact(
        self,
        artifact_hash: str,
        capabilities: Sequence[str] = (),
        approved_by: str = "local_user",
        ttl_days: int = 30,
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Approves a draft skill and issues a cryptographic grant."""
        artifact = self.get_artifact(artifact_hash)
        if not artifact:
            return False, None, "Artifact not found"

        # Verify integrity before approving
        current_hash = compute_artifact_hash(
            artifact.name, artifact.code, artifact.manifest, artifact.test_cases
        )
        if current_hash != artifact.artifact_hash:
            self.set_state(artifact.artifact_hash, STATE_QUARANTINE, reason="Hash mismatch during approval")
            return False, None, "Artifact integrity check failed: code was modified after staging"

        grant = issue_grant(
            artifact_hash=artifact.artifact_hash,
            capabilities=capabilities,
            approved_by=approved_by,
            ttl_days=ttl_days,
        )

        ok = self.set_state(
            artifact.artifact_hash,
            STATE_APPROVED,
            grant=grant,
            approved_by=approved_by,
        )
        if ok:
            return True, grant, "Approved"
        return False, None, "Failed to persist approval"

    def install_artifact(self, artifact_hash: str) -> Tuple[bool, str]:
        """Promotes an approved skill artifact to installed."""
        artifact = self.get_artifact(artifact_hash)
        if not artifact:
            return False, "Artifact not found"

        if artifact.state != STATE_APPROVED:
            return False, f"Cannot install artifact in '{artifact.state}' state (must be approved first)"

        if not artifact.grant:
            return False, "Artifact has no approval grant"

        # Verify grant before install
        grant_ok, grant_reason = verify_grant(artifact.grant, artifact.artifact_hash)
        if not grant_ok:
            return False, f"Grant verification failed: {grant_reason}"

        ok = self.set_state(artifact.artifact_hash, STATE_INSTALLED)
        return ok, "Installed" if ok else "Failed to transition state to installed"

    def reject_artifact(self, artifact_hash: str, reason: str = "") -> bool:
        """Rejects a skill draft."""
        return self.set_state(artifact_hash, STATE_REJECTED, reason=reason)

    def verify_installed_artifact(self, name: str) -> Tuple[bool, Optional[SkillArtifact], str]:
        """Verifies that an installed skill is authentic, untampered, and carries a valid grant."""
        artifact = self.get_artifact_by_name(name)
        if not artifact:
            return False, None, f"Skill '{name}' is not in skill store"

        if artifact.state != STATE_INSTALLED:
            return False, artifact, f"Skill '{name}' state is '{artifact.state}', not installed"

        current_hash = compute_artifact_hash(
            artifact.name, artifact.code, artifact.manifest, artifact.test_cases
        )
        if current_hash != artifact.artifact_hash:
            self.set_state(artifact.artifact_hash, STATE_QUARANTINE, reason="Tampering detected")
            return False, artifact, "Artifact code has been tampered with since installation"

        if not artifact.grant:
            return False, artifact, "Installed skill has no approval grant"

        grant_ok, grant_reason = verify_grant(artifact.grant, current_hash)
        if not grant_ok:
            return False, artifact, f"Installed grant is invalid: {grant_reason}"

        return True, artifact, "Skill is verified and installed"
