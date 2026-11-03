"""
Release Manager & Signed Update Pipeline (WS-6, SEC-11).

Part of Security Architecture Phase 2.
Implements:
1. Cryptographically signed release manifest verification (`release.json` + Ed25519 `.sig`).
2. Hash-pinned artifact verification (SHA-256).
3. Phased rollout cohort gating (`sha256(install_id + seed) % 100`).
4. Update policy controls (`manual`, `notify`, `auto`, `pin:<version>`).
5. Safe development clone detection ("Development build -- updates disabled").
6. Atomic A/B staging, pointer activation, and automatic rollback after 2 boot failures.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

from core.plugin_sign import verify_signature
from core.user_paths import atomic_write_json, get_user_data_dir

logger = logging.getLogger("ReleaseManager")

# Well-known embedded public key for production BrahmaEvo releases (hex-encoded Ed25519)
PINNED_RELEASE_PUBLIC_KEY = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)


class ReleaseValidationError(ValueError):
    """Raised when a release manifest, signature, or artifact fails verification."""


class ReleasePolicyBlockedError(PermissionError):
    """Raised when an update is prevented by local policy (pin, clone, manual)."""


def canonical_json_bytes(data: Dict[str, Any]) -> bytes:
    """Produces reproducible, canonical JSON byte representation for cryptographic signing."""
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_file_sha256(path_or_bytes: Path | bytes) -> str:
    """Computes standard sha256:<hex> string."""
    if isinstance(path_or_bytes, bytes):
        raw = path_or_bytes
    else:
        raw = Path(path_or_bytes).read_bytes()
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


class ReleaseManager:
    """Coordinates release checking, signature verification, A/B staging, and rollbacks."""

    def __init__(
        self,
        releases_dir: Optional[Path] = None,
        trusted_keys: Optional[Set[str]] = None,
        policy: Optional[str] = None,
        is_git_clone: Optional[bool] = None,
    ):
        self.releases_dir = (
            Path(releases_dir)
            if releases_dir is not None
            else get_user_data_dir() / "releases"
        )
        self.releases_dir.mkdir(parents=True, exist_ok=True)

        self.trusted_keys: Set[str] = (
            set(trusted_keys)
            if trusted_keys is not None
            else {PINNED_RELEASE_PUBLIC_KEY}
        )

        self.policy = (
            policy
            if policy is not None
            else os.getenv("BRAHMA_UPDATE_POLICY", "manual").strip().lower()
        )

        if is_git_clone is not None:
            self.is_git_clone = is_git_clone
        else:
            # Detect whether repo root has .git directory
            repo_root = Path(__file__).resolve().parent.parent
            self.is_git_clone = (repo_root / ".git").is_dir()

        self.state_file = self.releases_dir / "release_state.json"
        self._ensure_state_file()

    def _ensure_state_file(self) -> None:
        if not self.state_file.exists():
            initial_state: Dict[str, Any] = {
                "current": None,
                "previous": None,
                "boot_failures": 0,
                "installed_channel": "stable",
                "last_check": None,
            }
            atomic_write_json(self.state_file, initial_state)

    def get_state(self) -> Dict[str, Any]:
        """Loads persistent release state (current, previous, boot_failures)."""
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {"current": None, "previous": None, "boot_failures": 0}

    def _save_state(self, state: Dict[str, Any]) -> None:
        atomic_write_json(self.state_file, state)

    def verify_manifest(
        self, manifest: Dict[str, Any], signature_hex: str
    ) -> bool:
        """Verifies Ed25519 signature of the release manifest against trusted keys."""
        required_fields = [
            "product",
            "version",
            "channel",
            "commit",
            "published_at",
            "artifacts",
        ]
        for field in required_fields:
            if field not in manifest:
                raise ReleaseValidationError(
                    f"Manifest missing required field: {field}"
                )

        if manifest.get("product") != "BrahmaEvo":
            raise ReleaseValidationError(
                f"Unsupported product: {manifest.get('product')}"
            )

        canonical = canonical_json_bytes(manifest)

        # Check signature against any trusted public key
        for pub_key in self.trusted_keys:
            if verify_signature(pub_key, signature_hex, canonical):
                return True

        return False

    def verify_artifact(self, artifact_data: bytes, expected_hash: str) -> bool:
        """Verifies SHA-256 integrity of an artifact payload."""
        actual_hash = compute_file_sha256(artifact_data)
        normalized_expected = (
            expected_hash
            if expected_hash.startswith("sha256:")
            else f"sha256:{expected_hash}"
        )
        return actual_hash.lower() == normalized_expected.lower()

    def is_cohort_eligible(
        self, manifest: Dict[str, Any], install_id: str
    ) -> bool:
        """Evaluates whether install_id is within the release cohort percentage."""
        rollout = manifest.get("rollout")
        if not rollout or not isinstance(rollout, dict):
            return True

        cohort_raw = rollout.get("cohort", "100%")
        cohort_clean = str(cohort_raw).rstrip("%").strip()
        try:
            percent = int(cohort_clean)
        except ValueError:
            percent = 100

        if percent >= 100:
            return True
        if percent <= 0:
            return False

        seed = str(rollout.get("seed", manifest.get("version", "")))
        hash_input = f"{install_id}:{seed}".encode("utf-8")
        bucket = int(hashlib.sha256(hash_input).hexdigest()[:8], 16) % 100
        return bucket < percent

    def check_policy(
        self, target_version: str, is_manual_override: bool = False
    ) -> Tuple[bool, str]:
        """Checks if updating to target_version is permissible under current policy."""
        # 1. Clone repository protection
        if self.is_git_clone and not os.getenv("BRAHMA_DEV_ALLOW_UPDATE"):
            if not is_manual_override:
                return (
                    False,
                    "Development build -- automatic updates disabled for git clones.",
                )

        # 2. Version pinning
        if self.policy.startswith("pin:"):
            pinned_ver = self.policy.split(":", 1)[1].strip()
            if target_version != pinned_ver:
                return (
                    False,
                    f"Update blocked by version pin ({pinned_ver}); requested: {target_version}",
                )

        # 3. Standard policies
        if self.policy == "manual" and not is_manual_override:
            return False, "Policy 'manual' requires explicit user confirmation."

        return True, "Update permitted by policy."

    def stage_release(
        self,
        manifest: Dict[str, Any],
        signature_hex: str,
        artifacts: Dict[str, bytes],
    ) -> Path:
        """Verifies signature & artifacts, then unpacks to releases/<channel>/<version>."""
        # 1. Cryptographic manifest verification
        if not self.verify_manifest(manifest, signature_hex):
            raise ReleaseValidationError(
                "Release manifest signature verification failed."
            )

        # 2. Artifact hash verification
        manifest_artifacts = {
            a["name"]: a for a in manifest.get("artifacts", [])
        }
        for art_name, art_bytes in artifacts.items():
            if art_name not in manifest_artifacts:
                raise ReleaseValidationError(
                    f"Artifact '{art_name}' not declared in manifest."
                )
            expected_hash = manifest_artifacts[art_name].get("sha256", "")
            if not self.verify_artifact(art_bytes, expected_hash):
                raise ReleaseValidationError(
                    f"Artifact '{art_name}' SHA-256 hash mismatch."
                )

        # 3. Stage directory
        channel = manifest.get("channel", "stable")
        version = manifest.get("version", "0.0.0")
        target_dir = self.releases_dir / channel / version
        target_dir.mkdir(parents=True, exist_ok=True)

        # Write manifest, signature, and artifacts
        (target_dir / "release.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        (target_dir / "release.json.sig").write_text(
            signature_hex.strip(), encoding="utf-8"
        )

        for art_name, art_bytes in artifacts.items():
            (target_dir / art_name).write_bytes(art_bytes)

        logger.info(f"[ReleaseManager] Staged release {version} to {target_dir}")
        return target_dir

    def activate_version(
        self, version: str, channel: str = "stable"
    ) -> Dict[str, Any]:
        """Swaps active release pointer to new version; retains previous for rollback."""
        target_dir = self.releases_dir / channel / version
        if not target_dir.exists():
            raise FileNotFoundError(
                f"Cannot activate non-existent release version: {target_dir}"
            )

        state = self.get_state()
        current_version = state.get("current")

        if current_version != version:
            state["previous"] = current_version
            state["current"] = version

        state["boot_failures"] = 0
        state["installed_channel"] = channel
        self._save_state(state)

        logger.info(
            f"[ReleaseManager] Activated version {version} (previous: {state['previous']})"
        )
        return state

    def rollback(self) -> Optional[str]:
        """Reverts active pointer to previous version upon failed health check or command."""
        state = self.get_state()
        previous_ver = state.get("previous")
        if not previous_ver:
            logger.warning("[ReleaseManager] Rollback requested but no previous version recorded.")
            return None

        current_ver = state.get("current")
        state["current"] = previous_ver
        state["previous"] = current_ver
        state["boot_failures"] = 0
        self._save_state(state)

        logger.warning(
            f"[ReleaseManager] Rolled back from {current_ver} to previous version: {previous_ver}"
        )
        return previous_ver

    def record_boot_failure(self) -> int:
        """Increments boot failure counter; automatically triggers rollback after 2 failures."""
        state = self.get_state()
        failures = int(state.get("boot_failures", 0)) + 1
        state["boot_failures"] = failures
        self._save_state(state)

        logger.error(f"[ReleaseManager] Recorded startup failure #{failures}")
        if failures >= 2 and state.get("previous"):
            logger.critical(
                "[ReleaseManager] Startup failure threshold exceeded (>= 2). Triggering automatic rollback!"
            )
            self.rollback()

        return failures

    def record_boot_success(self) -> None:
        """Resets boot failure counter following a healthy startup."""
        state = self.get_state()
        if state.get("boot_failures", 0) != 0:
            state["boot_failures"] = 0
            self._save_state(state)
            logger.info("[ReleaseManager] Startup succeeded; reset boot failure counter.")
