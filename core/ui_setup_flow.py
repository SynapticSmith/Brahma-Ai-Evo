"""MainWindow's _SetupFlowMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

import json
import os
import traceback



def bind(*, API_FILE, CONFIG_DIR, SetupOverlay):
    globals().update(API_FILE=API_FILE, CONFIG_DIR=CONFIG_DIR, SetupOverlay=SetupOverlay)


class _SetupFlowMixin:
    def _show_setup(self, defaults: dict | None = None):
        try:
            if hasattr(self, '_floating_gesture_card'):
                self._floating_gesture_card.hide()
            if self._overlay:
                self._overlay.hide()
                self._overlay.deleteLater()
                self._overlay = None
            
            # Maximize the main window for the cinematic experience
            self.showMaximized()
            
            ov = SetupOverlay(self.centralWidget(), defaults=defaults or self._load_api_defaults())
            cw = self.centralWidget()
            ov.setGeometry(0, 0, cw.width(), cw.height())
            ov.done.connect(self._on_setup_done)
            ov.show()
            ov.raise_()
            ov.activateWindow()
            self._overlay = ov
        except Exception as e:
            import traceback
            with open('setup_crash.log', 'w') as err_f:
                err_f.write(traceback.format_exc())
            raise


    # Change signature:
    def _on_setup_done(self, key: str, or_key: str, os_name: str):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            existing = self._load_api_defaults()
            API_FILE.write_text(
                json.dumps({
                    "gemini_api_key":    key,
                    "openrouter_api_key": or_key,
                    "anthropic_api_key": existing.get("anthropic_api_key", ""),
                    "os_system":         os_name,
                }, indent=4),
                encoding="utf-8",
            )
            self._ready = True
            self._api_ready = True
            if self._overlay:
                self._overlay.hide()
                self._overlay.deleteLater()
                self._overlay = None
            if hasattr(self, '_floating_gesture_card'):
                self._floating_gesture_card.show()
            self.showNormal()
            self._apply_state("LISTENING")
            self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. Brahma Evo online.")
        except Exception as e:
            self._log.append_log(f"ERR: setup failed: {e}")
            traceback.print_exc()
