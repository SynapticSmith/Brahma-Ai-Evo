"""MainWindow's Discord settings/bot methods, moved verbatim out of ui.py
(P4-19, mixin split). Method bodies are byte-identical; module-level helpers
and config paths are injected via bind()."""
from __future__ import annotations

import json
import os

from core.ui_theme import (C)


def bind(*, CONFIG_DIR, DISCORD_SETTINGS_FILE, _default_discord_settings):
    globals().update(CONFIG_DIR=CONFIG_DIR, DISCORD_SETTINGS_FILE=DISCORD_SETTINGS_FILE, _default_discord_settings=_default_discord_settings)


class _DiscordMixin:
    def _load_discord_settings(self) -> dict:
        settings = _default_discord_settings()
        if DISCORD_SETTINGS_FILE.exists():
            try:
                data = json.loads(DISCORD_SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    settings.update(data)
            except Exception:
                pass
        if (settings.get("bot_token") or "").strip():
            settings["enabled"] = True
        return dict(settings)

    def _save_discord_settings(self, settings: dict):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        DISCORD_SETTINGS_FILE.write_text(json.dumps(settings, indent=4), encoding="utf-8")

    def _emit_discord_settings(self):
        if not hasattr(self, "_discord_token_input"):
            return
        settings = {
            "bot_token": self._discord_token_input.text().strip(),
            "enabled": bool(getattr(self, "_discord_enabled", False)),
            "channel_id": self._discord_channel_input.text().strip() if hasattr(self, "_discord_channel_input") else "",
        }
        self._save_discord_settings(settings)
        self.discord_config_changed.emit(dict(settings))
        self._refresh_discord_card()

    def _refresh_discord_card(self, note: str = ""):
        if not hasattr(self, "_discord_status_lbl"):
            return
        token = self._discord_token_input.text().strip() if hasattr(self, "_discord_token_input") else ""
        enabled = bool(getattr(self, "_discord_enabled", False))
        channel_id = self._discord_channel_input.text().strip() if hasattr(self, "_discord_channel_input") else ""
        if note:
            status = note
            color = C.PRI if "error" in note.lower() or "missing" in note.lower() else C.TEXT_MED
        elif not token:
            status = "Token required"
            color = C.PRI
        elif not channel_id:
            status = "Token saved - channel optional"
            color = C.TEXT_MED
        elif enabled:
            status = "Bot enabled"
            color = C.GREEN
        else:
            status = "Bot disabled"
            color = C.TEXT_MED
        self._discord_status_lbl.setText(status)
        self._discord_status_lbl.setStyleSheet(f"color: {color}; background: transparent;")
        if hasattr(self, "_discord_start_btn"):
            self._discord_start_btn.setText("Start Discord Bot")
        if hasattr(self, "_discord_stop_btn"):
            self._discord_stop_btn.setText("Stop Discord Bot")
        if hasattr(self, "_discord_save_btn"):
            self._discord_save_btn.setText("Save Settings")

    def _save_discord_token(self):
        if not hasattr(self, "_discord_token_input"):
            return
        token = self._discord_token_input.text().strip()
        channel_id = self._discord_channel_input.text().strip() if hasattr(self, "_discord_channel_input") else ""
        settings = self._load_discord_settings()
        settings["bot_token"] = token
        settings["channel_id"] = channel_id
        settings["enabled"] = bool(token)
        self._discord_enabled = bool(token)
        self._save_discord_settings(settings)
        self.discord_config_changed.emit(dict(settings))
        if token:
            self._log.append_log("SYS: Discord bot token saved and bot enabled.")
            self._refresh_discord_card("Token saved")
        else:
            self._log.append_log("SYS: Discord bot token cleared.")
            self._discord_enabled = False
            self._refresh_discord_card("Token required")

    def _start_discord_bot(self):
        if not hasattr(self, "_discord_token_input"):
            return
        token = self._discord_token_input.text().strip()
        channel_id = self._discord_channel_input.text().strip() if hasattr(self, "_discord_channel_input") else ""
        if not token:
            self._discord_enabled = False
            self._save_discord_token()
            return
        self._discord_enabled = True
        settings = {
            "bot_token": token,
            "enabled": True,
            "channel_id": channel_id,
        }
        self._save_discord_settings(settings)
        self.discord_config_changed.emit(dict(settings))
        self._log.append_log("SYS: Discord bot started.")
        self._refresh_discord_card()

    def _stop_discord_bot(self):
        if not hasattr(self, "_discord_token_input"):
            return
        token = self._discord_token_input.text().strip()
        channel_id = self._discord_channel_input.text().strip() if hasattr(self, "_discord_channel_input") else ""
        self._discord_enabled = False
        settings = {
            "bot_token": token,
            "enabled": False,
            "channel_id": channel_id,
        }
        self._save_discord_settings(settings)
        self.discord_config_changed.emit(dict(settings))
        self._log.append_log("SYS: Discord bot stopped.")
        self._refresh_discord_card()
