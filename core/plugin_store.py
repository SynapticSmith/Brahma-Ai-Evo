"""
Plugin Store, Trust Store, and Revocation Engine (WS-4, SEC-09).

Part of Security Architecture Phase 2.
Implements:
1. Isolated store for cryptographically signed `.bhplugin` bundles (`plugins/store/<id>/<version>`).
2. Trust store management with pinned publisher public keys (`trust_store.json`).
3. Publisher key and plugin ID revocation list checking (`revocations.json`).
4. Content-addressed installation, indexing (`index.json`), and lifecycle control.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
import shutil
from typing import Any, Dict, Optional, Set, Tuple
import zipfile

from core.plugin_sign import PluginVerifier
from core.user_paths import atomic_write_json, get_user_data_dir

logger = logging.getLogger("PluginStore")


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class PluginInstallationError(RuntimeError):
    """Raised when plugin bundle installation fails validation or verification."""


class PluginStore:
    """Manages verified plugin installations, trust store, and revocations."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = (
            Path(base_dir)
            if base_dir is not None
            else get_user_data_dir() / "plugins"
        )
        self.store_dir = self.base_dir / "store"
        self.index_file = self.base_dir / "index.json"
        self.trust_file = self.base_dir / "trust_store.json"
        self.revocations_file = self.base_dir / "revocations.json"

        self._ensure_layout()

    def _ensure_layout(self) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_file.exists():
            atomic_write_json(self.index_file, {})
        if not self.trust_file.exists():
            atomic_write_json(self.trust_file, {"trusted_keys": {}})
        if not self.revocations_file.exists():
            atomic_write_json(
                self.revocations_file,
                {"revoked_keys": {}, "revoked_plugins": {}},
            )

    # Trust Store Management
    def get_trusted_keys(self) -> Set[str]:
        """Returns set of all hex-encoded trusted publisher public keys."""
        try:
            data = json.loads(self.trust_file.read_text(encoding="utf-8"))
            keys_dict = data.get("trusted_keys", {})
            return {k.lower().strip() for k in keys_dict.keys()}
        except Exception:
            return set()

    def add_trusted_publisher(self, key_hex: str, publisher_name: str) -> None:
        """Adds a publisher public key to the trusted publisher store."""
        key_clean = key_hex.lower().strip()
        try:
            data = json.loads(self.trust_file.read_text(encoding="utf-8"))
        except Exception:
            data = {"trusted_keys": {}}
        data.setdefault("trusted_keys", {})[key_clean] = {
            "publisher": publisher_name,
            "added_at": _now_iso(),
        }
        atomic_write_json(self.trust_file, data)
        logger.info(f"[PluginStore] Added trusted publisher '{publisher_name}' ({key_clean[:16]}...)")

    def remove_trusted_publisher(self, key_hex: str) -> bool:
        """Removes a publisher key from the trust store."""
        key_clean = key_hex.lower().strip()
        try:
            data = json.loads(self.trust_file.read_text(encoding="utf-8"))
        except Exception:
            return False
        if key_clean in data.get("trusted_keys", {}):
            del data["trusted_keys"][key_clean]
            atomic_write_json(self.trust_file, data)
            return True
        return False

    # Revocation Management
    def get_revoked_keys(self) -> Set[str]:
        """Returns set of revoked publisher public keys."""
        try:
            data = json.loads(self.revocations_file.read_text(encoding="utf-8"))
            return {k.lower().strip() for k in data.get("revoked_keys", {}).keys()}
        except Exception:
            return set()

    def get_revoked_plugins(self) -> Set[str]:
        """Returns set of revoked plugin IDs."""
        try:
            data = json.loads(self.revocations_file.read_text(encoding="utf-8"))
            return {p.strip() for p in data.get("revoked_plugins", {}).keys()}
        except Exception:
            return set()

    def revoke_publisher(self, key_hex: str, reason: str = "Unspecified") -> None:
        """Adds a publisher key to the revocation list."""
        key_clean = key_hex.lower().strip()
        try:
            data = json.loads(self.revocations_file.read_text(encoding="utf-8"))
        except Exception:
            data = {"revoked_keys": {}, "revoked_plugins": {}}
        data.setdefault("revoked_keys", {})[key_clean] = {
            "reason": reason,
            "revoked_at": _now_iso(),
        }
        atomic_write_json(self.revocations_file, data)
        logger.warning(f"[PluginStore] REVOKED publisher key {key_clean[:16]}...: {reason}")

    def revoke_plugin(self, plugin_id: str, reason: str = "Unspecified") -> None:
        """Adds a plugin ID to the revocation list."""
        pid_clean = plugin_id.strip()
        try:
            data = json.loads(self.revocations_file.read_text(encoding="utf-8"))
        except Exception:
            data = {"revoked_keys": {}, "revoked_plugins": {}}
        data.setdefault("revoked_plugins", {})[pid_clean] = {
            "reason": reason,
            "revoked_at": _now_iso(),
        }
        atomic_write_json(self.revocations_file, data)
        logger.warning(f"[PluginStore] REVOKED plugin ID {pid_clean}: {reason}")

    def is_revoked(
        self, key_hex: str, plugin_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """Checks if a publisher key or plugin ID is revoked."""
        key_clean = key_hex.lower().strip()
        revoked_keys = self.get_revoked_keys()
        if key_clean in revoked_keys:
            return True, f"Publisher key '{key_clean[:16]}...' is revoked."

        if plugin_id:
            revoked_pids = self.get_revoked_plugins()
            if plugin_id.strip() in revoked_pids:
                return True, f"Plugin ID '{plugin_id}' is revoked."

        return False, None

    # Plugin Lifecycle & Indexing
    def install_bundle(self, bundle_path: Path | str) -> Dict[str, Any]:
        """Validates signatures, hashes, revocations, and unpacks into isolated store."""
        path = Path(bundle_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Plugin bundle file not found: {path}")

        if not zipfile.is_zipfile(path):
            raise PluginInstallationError(f"Bundle is not a valid zip archive: {path.name}")

        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            if "plugin.json" not in names:
                raise PluginInstallationError("Bundle missing 'plugin.json' manifest.")
            if "signature.ed25519" not in names:
                raise PluginInstallationError("Bundle missing 'signature.ed25519' signature.")

            try:
                manifest = json.loads(zf.read("plugin.json").decode("utf-8"))
            except Exception as exc:
                raise PluginInstallationError(f"Malformed plugin.json: {exc}")

            signature_hex = zf.read("signature.ed25519").decode("utf-8").strip()

            payload_files = {}
            for name in names:
                if name.startswith("payload/"):
                    payload_files[name] = zf.read(name)

        plugin_id = str(manifest.get("id") or "").strip()
        version = str(manifest.get("version") or "0.1.0").strip()
        pub_key = str(manifest.get("publisher_key") or "").strip().lower()

        if not plugin_id:
            raise PluginInstallationError("Plugin manifest missing 'id'.")

        # 1. Check revocation list
        is_rev, rev_reason = self.is_revoked(pub_key, plugin_id)
        if is_rev:
            raise PluginInstallationError(f"Cannot install revoked plugin: {rev_reason}")

        # 2. Check trust store
        trusted_keys = self.get_trusted_keys()
        # If trust store is populated, require key to be present
        if trusted_keys and pub_key not in trusted_keys:
            raise PluginInstallationError(
                f"Publisher key '{pub_key[:16]}...' is not in the trusted publisher store."
            )

        # 3. Verify cryptographic bundle authenticity and hashes
        verifier = PluginVerifier(
            trusted_keys=trusted_keys,
            revoked_keys=self.get_revoked_keys(),
        )
        ok, reason = verifier.verify_bundle(manifest, payload_files, signature_hex)
        if not ok:
            raise PluginInstallationError(f"Bundle verification failed: {reason}")

        # 4. Unpack into store/<id>/<version>/
        target_dir = self.store_dir / plugin_id / version
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)

        # Write manifest, signature, and extract payload
        (target_dir / "plugin.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        (target_dir / "signature.ed25519").write_text(
            signature_hex, encoding="utf-8"
        )

        with zipfile.ZipFile(path, "r") as zf:
            for name in zf.namelist():
                if name.startswith("payload/"):
                    # Safe extract avoiding traversal
                    rel_p = Path(name)
                    if ".." in rel_p.parts:
                        continue
                    dest = target_dir / rel_p
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(name))

        # 5. Update index.json
        index = self.get_installed_plugins()
        index[plugin_id] = {
            "version": version,
            "name": manifest.get("name", plugin_id),
            "publisher": manifest.get("publisher", "Unknown"),
            "publisher_key": pub_key,
            "entry": manifest.get("entry", "payload/main.py:register"),
            "capabilities": manifest.get("capabilities", []),
            "path": str(target_dir),
            "installed_at": _now_iso(),
            "enabled": True,
        }
        atomic_write_json(self.index_file, index)
        logger.info(f"[PluginStore] Installed plugin '{plugin_id}' v{version} to {target_dir}")
        return manifest

    def uninstall_plugin(self, plugin_id: str) -> bool:
        """Removes a plugin from the store and unregisters it from index.json."""
        index = self.get_installed_plugins()
        pid = plugin_id.strip()
        if pid not in index:
            return False

        plugin_info = index.pop(pid)
        target_dir = Path(plugin_info.get("path", ""))
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)

        atomic_write_json(self.index_file, index)
        logger.info(f"[PluginStore] Uninstalled plugin '{pid}'")
        return True

    def get_installed_plugins(self) -> Dict[str, Any]:
        """Returns dictionary of currently installed plugins from index.json."""
        try:
            return json.loads(self.index_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def get_plugin_path(self, plugin_id: str) -> Optional[Path]:
        """Returns the directory path for an installed plugin, or None if not installed."""
        info = self.get_installed_plugins().get(plugin_id.strip())
        if info and "path" in info:
            p = Path(info["path"])
            return p if p.exists() else None
        return None
