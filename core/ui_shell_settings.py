"""BrahmaUI's _ShellSettingsMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

import json
import os

from core.user_paths import (atomic_write_json)


def bind(*, APP_SETTINGS_FILE, CONFIG_DIR, _default_app_settings):
    globals().update(APP_SETTINGS_FILE=APP_SETTINGS_FILE, CONFIG_DIR=CONFIG_DIR, _default_app_settings=_default_app_settings)


class _ShellSettingsMixin:
    def _load_app_settings(self) -> dict:
        if self._app_settings_cache is not None:
            return dict(self._app_settings_cache)
        settings = _default_app_settings()
        if APP_SETTINGS_FILE.exists():
            try:
                data = json.loads(APP_SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        settings[k] = v
            except Exception:
                pass
        self._app_settings_cache = dict(settings)
        self._app_settings_cache_stamp = self._settings_file_stamp()
        return dict(settings)

    def _settings_file_stamp(self):
        """See MainWindow._settings_file_stamp."""
        try:
            if APP_SETTINGS_FILE.exists():
                st = APP_SETTINGS_FILE.stat()
                return (st.st_mtime_ns, st.st_size)
        except Exception:
            pass
        return None

    def _invalidate_app_settings_cache(self):
        """See MainWindow._invalidate_app_settings_cache: a second settings owner
        (core/provider_router.py, core/instruction_injector.py) writes the same
        file without touching this cache, so a stale snapshot must never be
        written back."""
        self._app_settings_cache = None

    def _save_app_settings(self, settings: dict):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        # Merge over the live file rather than trusting the caller's snapshot,
        # so a key another component changed after this dict was read survives.
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

    def _save_launcher_position(self, x: int, y: int):
        try:
            settings = self._load_app_settings()
            settings["launcher_pos"] = [int(x), int(y)]
            self._save_app_settings(settings)
        except Exception:
            pass
