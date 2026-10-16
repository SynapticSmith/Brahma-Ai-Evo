"""MainWindow's _AppSettingsMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

import json
import os
import platform

from core.user_paths import (atomic_write_json)


def bind(*, APP_SETTINGS_FILE, CONFIG_DIR, _current_boot_stamp, _default_app_settings):
    globals().update(APP_SETTINGS_FILE=APP_SETTINGS_FILE, CONFIG_DIR=CONFIG_DIR, _current_boot_stamp=_current_boot_stamp, _default_app_settings=_default_app_settings)


class _AppSettingsMixin:
    def _settings_file_stamp(self):
        """Identity of the settings file as it is on disk right now."""
        try:
            if APP_SETTINGS_FILE.exists():
                st = APP_SETTINGS_FILE.stat()
                return (st.st_mtime_ns, st.st_size)
        except Exception:
            pass
        return None

    def _load_app_settings(self) -> dict:
        # A plain cache is not safe here: core/provider_router.py and
        # core/instruction_injector.py write this same file through
        # memory.config_manager without knowing this cache exists. Serving the
        # cached snapshot after such a write makes the UI believe the old value,
        # and the next save writes that old value back - which is how disabling
        # OpenRouter reverted after an unrelated save. Re-read whenever the file
        # on disk has changed, so the two owners can never diverge.
        stamp = self._settings_file_stamp()
        if self._app_settings_cache is not None and stamp == getattr(
            self, "_app_settings_cache_stamp", None
        ):
            return dict(self._app_settings_cache)
        settings = _default_app_settings()
        if APP_SETTINGS_FILE.exists():
            try:
                data = json.loads(APP_SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    settings.update(data)
            except Exception:
                pass
        self._app_settings_cache = dict(settings)
        self._app_settings_cache_stamp = self._settings_file_stamp()
        return dict(settings)

    def _invalidate_app_settings_cache(self):
        """Force the next load to re-read the file.

        Settings have more than one owner. The cards write through here, but
        core/provider_router.py and core/instruction_injector.py write the same
        file through memory.config_manager and never touch this cache. Left
        stale, the cache carries the OLD value and a later save writes it back,
        silently reverting the other component's change. That is precisely how
        disabling OpenRouter kept coming back after an unrelated save.
        """
        self._app_settings_cache = None

    def _save_app_settings(self, settings: dict):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        # Merge over what is actually on disk instead of trusting the caller's
        # snapshot: another component may have written a key since this dict
        # was loaded, and a blind replace would revert it.
        merged = dict(settings)
        if APP_SETTINGS_FILE.exists():
            try:
                on_disk = json.loads(APP_SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(on_disk, dict):
                    merged = on_disk
                    merged.update(settings)
            except Exception:
                pass
        atomic_write_json(APP_SETTINGS_FILE, merged)
        self._app_settings_cache = dict(merged)

    def _startup_animation_enabled(self) -> bool:
        if platform.system() != "Windows":
            return False
        return bool(self._load_app_settings().get("startup_animation_enabled", True))

    def _set_startup_animation_enabled(self, enabled: bool) -> bool:
        try:
            settings = self._load_app_settings()
            settings["startup_animation_enabled"] = bool(enabled)
            settings["last_boot_stamp"] = _current_boot_stamp()
            self._save_app_settings(settings)
            return True
        except Exception as e:
            self._log.append_log("ERR: startup animation setting failed: %s" % e)
            return False

    def _refresh_startup_animation_button(self):
        if not hasattr(self, "_startup_anim_btn"):
            return
        if platform.system() != "Windows":
            self._startup_anim_btn.setText("Startup Animation (Windows only)")
            self._startup_anim_btn.setEnabled(False)
            return
        if self._startup_animation_enabled():
            self._startup_anim_btn.setText("Disable Startup Animation")
        else:
            self._startup_anim_btn.setText("Enable Startup Animation")

    def _toggle_startup_animation(self):
        if platform.system() != "Windows":
            return
        enabled = not self._startup_animation_enabled()
        if self._set_startup_animation_enabled(enabled):
            self._refresh_startup_animation_button()
            state = "enabled" if enabled else "disabled"
            self._log.append_log("SYS: Startup animation %s." % state)
