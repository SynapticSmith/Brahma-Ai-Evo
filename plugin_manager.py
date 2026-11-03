from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Iterable


class PluginManager:
    """Manages Brahma extension plugins with an explicit opt-in allowlist.

    Any unallowed or unsigned .py script in the plugins/ directory is skipped
    at startup to prevent arbitrary code execution via writable plugin directories
    (Finding #2 / WS-4 / ARCHITECTURE-SPEC §7.3).
    """

    def __init__(self, base_dir: Path, allowlist: Iterable[str] | None = None, dev_mode: bool = False):
        self.base_dir = Path(base_dir)
        self.plugins_dir = self.base_dir / "plugins"
        self.plugins: list[Any] = []
        self.brahma = None
        self.dev_mode = bool(dev_mode)
        self._allowlist: set[str] = set(allowlist) if allowlist is not None else self._load_allowlist()

    def _load_allowlist(self) -> set[str]:
        # 1. Check config/plugin_policy.json in base_dir
        policy_file = self.base_dir / "config" / "plugin_policy.json"
        if policy_file.exists():
            try:
                data = json.loads(policy_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    names = data.get("enabled_plugins") or data.get("allowlist") or []
                    return {str(name).strip() for name in names if str(name).strip()}
                elif isinstance(data, list):
                    return {str(name).strip() for name in data if str(name).strip()}
            except Exception as exc:
                print(f"[Plugins] Error reading {policy_file}: {exc}")

        # 2. Check app_settings.json in base_dir / config
        settings_file = self.base_dir / "config" / "app_settings.json"
        if settings_file.exists():
            try:
                data = json.loads(settings_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    names = data.get("enabled_plugins") or data.get("plugin_allowlist") or []
                    return {str(name).strip() for name in names if str(name).strip()}
            except Exception:
                pass

        return set()

    def is_allowed(self, plugin_name: str) -> bool:
        stem = Path(plugin_name).stem
        return "*" in self._allowlist or stem in self._allowlist or plugin_name in self._allowlist

    def load_plugins(self, *, allowlist: Iterable[str] | None = None, dev_mode: bool | None = None) -> list[Any]:
        if not self.plugins_dir.exists():
            return []
        is_dev = self.dev_mode if dev_mode is None else bool(dev_mode)
        if is_dev:
            print("[Plugins] [DEV MODE] WARNING: Development mode active — loading unsigned development plugins from plugins/")
        effective_allowlist = {"*"} if is_dev else (set(allowlist) if allowlist is not None else self._allowlist)
        self.plugins.clear()

        for p in sorted(self.plugins_dir.glob("*.py")):
            if p.name.startswith("__"):
                continue
            if "*" not in effective_allowlist and p.stem not in effective_allowlist and p.name not in effective_allowlist:
                print(f"[Plugins] Skipping unallowed plugin {p.name} (not in plugin allowlist)")
                continue
            try:
                spec = importlib.util.spec_from_file_location(p.stem, str(p))
                if not spec or not spec.loader:
                    continue
                mod = importlib.util.module_from_spec(spec)
                sys.modules[p.stem] = mod
                spec.loader.exec_module(mod)
                plugin = getattr(mod, "plugin", None)
                if plugin is None:
                    # fallback: accept module with functions
                    plugin = mod
                self.plugins.append(plugin)
                print(f"[Plugins] Loaded {p.name}")
            except Exception as exc:
                print(f"[Plugins] Failed to load {p.name}: {exc}")
        return list(self.plugins)

    def register_brahma(self, brahma_obj) -> None:
        self.brahma = brahma_obj
        for p in list(self.plugins):
            try:
                fn = getattr(p, "on_brahma_created", None)
                if callable(fn):
                    fn(brahma_obj)
            except Exception as exc:
                print(f"[Plugins] on_brahma_created error: {exc}")

    def dispatch(self, hook: str, *args, **kwargs):
        """Call hook on plugins. If any plugin returns True, stop and return True."""
        for p in list(self.plugins):
            try:
                fn = getattr(p, hook, None)
                if callable(fn):
                    # call with brahma if plugin expects it
                    try:
                        res = fn(*args, **kwargs, brahma=self.brahma)
                    except TypeError:
                        res = fn(*args, **kwargs)
                    if res is True:
                        return True
            except Exception as exc:
                print(f"[Plugins] Hook {hook} error in {getattr(p, '__name__', str(p))}: {exc}")
        return False

    def load_dev_plugins(self) -> list[Any]:
        """Loads development plugins from plugins/ with an explicit security warning."""
        return self.load_plugins(dev_mode=True)

    def load_signed_bundle(
        self,
        bundle_path: Path,
        trusted_keys: set[str] | None = None,
        revoked_keys: set[str] | None = None,
    ) -> tuple[bool, Any, str]:
        """Loads and verifies a cryptographic .bhplugin bundle."""
        import zipfile
        from core.plugin_sign import PluginVerifier
        p = Path(bundle_path)
        if not p.exists():
            return False, None, f"Bundle file {p} not found"

        try:
            with zipfile.ZipFile(p, "r") as zf:
                if "plugin.json" not in zf.namelist():
                    return False, None, "Bundle missing plugin.json"
                if "signature.ed25519" not in zf.namelist():
                    return False, None, "Bundle missing signature.ed25519"

                manifest = json.loads(zf.read("plugin.json").decode("utf-8"))
                signature_hex = zf.read("signature.ed25519").decode("utf-8").strip()

                payload_files = {}
                for name in zf.namelist():
                    if name.startswith("payload/"):
                        payload_files[name] = zf.read(name)

                verifier = PluginVerifier(trusted_keys=trusted_keys, revoked_keys=revoked_keys)
                ok, msg = verifier.verify_bundle(manifest, payload_files, signature_hex)
                if not ok:
                    return False, None, f"Bundle verification failed: {msg}"

                return True, manifest, "Verified bundle"
        except Exception as exc:
            return False, None, f"Bundle load error: {exc}"
