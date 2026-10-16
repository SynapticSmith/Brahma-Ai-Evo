"""BrahmaUI's _ShellPanelSettingsMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

import subprocess



def bind(*, BASE_DIR, _OS, _hidden_launch_args, _launched_from_windows_startup):
    globals().update(BASE_DIR=BASE_DIR, _OS=_OS, _hidden_launch_args=_hidden_launch_args, _launched_from_windows_startup=_launched_from_windows_startup)


class _ShellPanelSettingsMixin:
    def _restart_app(self):
        try:
            args = _hidden_launch_args("--startup" if _launched_from_windows_startup() else "")
            args = [arg for arg in args if arg]
            kwargs = {"cwd": str(BASE_DIR)}
            if _OS == "Windows":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(args, **kwargs)
        except Exception as exc:
            self._win.write_log(f"ERR: Restart failed: {exc}")
        self._app.quit()

    def _on_panel_setting_toggle(self, key: str, value: bool):
        """Apply a toggle from the launcher control panel or the Settings card.

        Provider flags go through core.provider_router so the kill switch is
        honoured in one place; the rest persist to app_settings.json. Every step
        reports through _log rather than swallowing, because a silently-failing
        toggle is worse than a visible error -- the user cannot tell "off" from
        "broken".
        """
        persisted = False
        try:
            settings = self._load_app_settings()
            settings[key] = bool(value)
            self._save_app_settings(settings)
            persisted = True
        except Exception as exc:
            try:
                self._win.write_log(f"ERR: could not save {key}: {exc}")
            except Exception:
                pass
            return False

        try:
            if key.startswith("provider_enabled_"):
                from core import provider_router

                pid = key.replace("provider_enabled_", "")
                provider_router.set_enabled(pid, bool(value))
                # provider_router writes the file directly; invalidate the UI's
                # cached snapshot so a later save cannot re-write the stale
                # pre-toggle value over it.
                self._win._invalidate_app_settings_cache()
                chain = provider_router.describe()
                self._win.write_log(
                    f"SYS: Provider '{pid}' {'ENABLED' if value else 'DISABLED'}. "
                    f"Chain: {chain}"
                )
                return True
            if key == "enable_3d_background":
                bg = getattr(self._win, "_bg_widget", None)
                readback = {}
                if bg is not None and hasattr(bg, "set_animation_enabled"):
                    readback = bg.set_animation_enabled(bool(value)) or {}
                state = "enabled" if value else "disabled"
                if not readback.get("web_view"):
                    self._win.write_log(
                        f"ERR: 3D background {state} but no web view exists "
                        f"(WEB_ENGINE_AVAILABLE={readback.get('engine_available')}). "
                        f"Restart to apply."
                    )
                else:
                    self._win.write_log(
                        f"SYS: 3D background animation {state} "
                        f"(js={'issued' if readback.get('js_issued') else 'NOT issued'})."
                    )
                return True
            if key == "memory_extraction_enabled":
                self._win.write_log(
                    f"SYS: Background memory extraction "
                    f"{'ENABLED' if value else 'DISABLED'}."
                )
                return True
        except Exception as exc:
            try:
                self._win.write_log(f"ERR: toggle {key} saved but not applied: {exc}")
            except Exception:
                pass
            return False
        return persisted
