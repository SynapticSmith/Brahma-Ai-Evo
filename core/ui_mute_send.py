"""MainWindow's _MuteSendMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

import threading

from core.ui_thread import run_on_ui
from core.ui_theme import (C)
from sound_manager import (sound_mgr)


class _MuteSendMixin:
    def _toggle_mute(self):
        self._muted = not self._muted
        self._wakeword_listening = self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
            sound_mgr.play_listening_stop()
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")
            sound_mgr.play_listening_start()

    def set_muted_state(self, muted: bool, *, wakeword: bool = False):
        muted = bool(muted)
        if muted == self._muted and not wakeword:
            return
        self._muted = muted
        self.hud.muted = self._muted
        self._wakeword_listening = bool(muted)
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
            sound_mgr.play_listening_stop()
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")
            sound_mgr.play_listening_start()

    def _style_mute_btn(self):
        if not hasattr(self, "_mute_btn") or self._mute_btn is None:
            return
        if self._muted:
            self._mute_btn.setText("Muted")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #1a1010; color: {C.RED};
                    border: 1px solid {C.RED}; border-radius: 16px;
                }}
            """)
        else:
            self._mute_btn.setText("Mic")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(16,16,16,235); color: {C.WHITE};
                    border: 1px solid {C.BORDER_B}; border-radius: 16px;
                }}
                QPushButton:hover {{ border: 1px solid {C.WHITE}; }}
            """)

    def _send(self, txt: str = ""):
        if not txt:
            txt = self._input.text().strip()
            self._input.clear()
        if not txt:
            return
        self.submit_command(txt)

    def submit_command(self, txt: str, source: str = "local"):
        def _do():
            txt_norm = (txt or "").strip()
            if not txt_norm:
                return
            self._chat_source_queue.append(source or "local")
            if hasattr(self, "_command_card"):
                preview = txt_norm[:60] + ("…" if len(txt_norm) > 60 else "")
                self._command_card.set_body(preview)
                self._command_card.hide()
            if hasattr(self, "_result_card"):
                self._result_card.set_body("Waiting for reply...")
                self._result_card.hide()
            self._restart_card_hide_timer()
            self._log_sig.emit(f"You: {txt_norm}")
            if self.on_text_command:
                threading.Thread(target=self.on_text_command, args=(txt_norm, source or "local"), daemon=True).start()

        run_on_ui(self, _do)

    def _on_log_text(self, text: str):
        self._log.append_log(text)
        raw = (text or "").strip()
        low = raw.lower()
        if hasattr(self, "_result_card") and low.startswith("you:"):
            user_msg = raw.split(":", 1)[1].strip()
            source = self._chat_source_queue[0] if self._chat_source_queue else "local"
            if self.on_chat_event and user_msg:
                try:
                    self.on_chat_event({"role": "user", "text": user_msg, "source": source})
                except Exception:
                    pass
        if hasattr(self, "_result_card") and low.startswith("brahma evo:"):
            reply = raw.split(":", 1)[1].strip()
            self._result_card.set_body(reply[:80] + ("…" if len(reply) > 80 else ""))
            self._result_card.hide()
            self._restart_card_hide_timer()
            source = self._chat_source_queue[0] if self._chat_source_queue else "local"
            if self.on_chat_event and reply:
                try:
                    self.on_chat_event({"role": "assistant", "text": reply, "source": source})
                except Exception:
                    pass
            if self._chat_source_queue:
                self._chat_source_queue.popleft()
        elif hasattr(self, "_result_card") and low.startswith("err:"):
            self._result_card.set_body(raw.split(":", 1)[1].strip())
            self._result_card.hide()
            self._restart_card_hide_timer()
            source = self._chat_source_queue[0] if self._chat_source_queue else "local"
            if self.on_chat_event:
                try:
                    self.on_chat_event({"role": "system", "text": raw.split(":", 1)[1].strip(), "source": source})
                except Exception:
                    pass
            if self._chat_source_queue:
                self._chat_source_queue.popleft()
