"""BrahmaUI's _ShellActionsMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations



class _ShellActionsMixin:
    def _sync_launcher_state(self, state: str):
        state = (state or "idle").strip().lower()
        detail = {
            "listening": "Ready",
            "thinking": "Thinking...",
            "processing": "Executing task...",
            "speaking": "Speaking",
            "muted": "Muted",
        }.get(state, "Ready")
        try:
            self._launcher.set_state(state, detail)
        except Exception:
            pass

    def _on_clipboard_actionable(self, category: str, content: str):
        label = {
            "error_traceback": "Traceback/Error copied — want an explanation?",
            "json_data": "JSON copied — format/validate?",
            "sql_query": "SQL Query copied — optimize/review?",
            "code_snippet": "Code snippet copied — review?",
        }.get(category, "Clipboard item copied")
        self._workspace_sidebar.record_chat_event({
            "role": "system",
            "text": f"💡 {label}",
            "source": "clipboard",
        })

    def _submit_command(self, text: str):
        self._win.submit_command(text)

    def _browse_attachment(self):
        self._win._browse_attachment()

    def _toggle_mute(self):
        self._win._toggle_mute()

    def _show_update_prompt(self, remote_hash: str):
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self._win,
            "Update Detected!",
            f"A new update is available on GitHub!\n\nDo you want to apply the update and restart the app?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if reply == QMessageBox.StandardButton.Yes:
            from core.updater import apply_update_and_restart
            # Show a simple overlay while updating
            self._win._set_status("Applying update from GitHub...")
            import threading
            threading.Thread(target=apply_update_and_restart, daemon=True).start()

    def _on_chat_event(self, event: dict):
        try:
            self._discord_service.mirror_chat_event(event or {})
        except Exception:
            pass
        try:
            self._workspace_sidebar.record_chat_event(event or {})
        except Exception:
            pass
        try:
            self._win._inline_workspace.record_chat_event(event or {})
        except Exception:
            pass

    def _on_discord_config_changed(self, settings: dict):
        settings = settings or {}
        enabled = bool(settings.get("enabled", False) or (settings.get("bot_token") or "").strip())
        token = (settings.get("bot_token") or "").strip()
        channel_id = (settings.get("channel_id") or "").strip()
        self._discord_service.set_target_channel_id(channel_id)
        if not enabled or not token:
            self._discord_service.stop()
            if not token:
                self._win.discord_status_changed.emit("Token required")
            else:
                self._win.discord_status_changed.emit("Discord bot disabled")
            return
        try:
            self._discord_service.start(token)
        except Exception as exc:
            msg = f"Discord error: {exc}"
            self._win.discord_status_changed.emit(msg)
            self._win._log_sig.emit(f"ERR: {msg}")

    def _open_app(self):
        self._command_bar.hide()
        self._workspace_sidebar.hide_workspace(animate=False)
        self.show_main()
