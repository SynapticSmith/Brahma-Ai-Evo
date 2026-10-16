"""BrahmaUI's _ShellDelegatesMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

import time

from pathlib import (Path)

from core.ui_thread import run_on_ui


class _ShellDelegatesMixin:
    def _ui(self, fn):
        """Run a MainWindow widget call on the GUI thread.

        BrahmaUI delegates are reachable from worker threads (Discord, wake
        word, meeting/screen monitors, phone gateway). Touching QWidgets off
        the GUI thread raises ``QObject::setParent: ... different thread`` and
        corrupts layout state, so every direct widget call funnels through
        MainWindow._run_on_ui, which queues to the GUI thread when needed.
        """
        run_on_ui(getattr(self, "_win", None), fn)

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._ui(lambda: self._win._toggle_mute())

    def set_muted(self, muted: bool):
        if bool(muted) != self._win._muted:
            self._ui(lambda: self._win.set_muted_state(bool(muted)))

    def set_muted_state(self, muted: bool, *, wakeword: bool = False):
        self._ui(lambda: self._win.set_muted_state(bool(muted), wakeword=wakeword))

    @property
    def current_file(self) -> str | None:
        return self._win._current_file

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_attention_action(self):
        return self._win.on_attention_action

    @on_attention_action.setter
    def on_attention_action(self, cb):
        self._win.on_attention_action = cb

    @property
    def on_chat_event(self):
        return self._win.on_chat_event

    @on_chat_event.setter
    def on_chat_event(self, cb):
        self._win.on_chat_event = cb

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    # The Always On / On Call controls are declared on MainWindow. main.py
    # wires them through BrahmaUI, so they have to be reachable here -- they
    # were not, and the resulting AttributeError was swallowed by the caller,
    # leaving the whole feature silently inert.
    @property
    def live_mode_changed(self):
        return self._win.live_mode_changed

    @property
    def live_call_toggled(self):
        return self._win.live_call_toggled

    def set_audio_level(self, level: float):
        self._ui(lambda: self._win.set_audio_level(level))

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def show_confirm(self, title: str, detail: str = ""):
        self.w.show_confirm(title, detail)

    def hide_confirm(self):
        self.w.hide_confirm()

    def show_memory_inspector(self):
        self.w.show_memory_inspector()

    def hide_memory_inspector(self):
        self.w.hide_memory_inspector()

    def show_daily_briefing(self, text: str):
        self._ui(lambda: self._win.show_daily_briefing(text))

    def hide_daily_briefing(self):
        self._ui(lambda: self._win.hide_daily_briefing())

    def schedule_daily_briefing_hide(self):
        self._ui(lambda: self._win.schedule_daily_briefing_hide())

    def submit_external_command(self, text: str, source: str = "discord"):
        self._ui(lambda: self._win.submit_command(text, source=source))

    def notify_phone_connected(self):
        self._ui(lambda: self._win.notify_phone_connected())

    def set_scanning(self, enabled: bool, text: str = ""):
        self._ui(lambda: self._win.set_scanning(enabled, text))

    def show_circuit_hud(self, circuit_data: dict):
        self._win._circuit_hud_sig.emit(circuit_data or {})

    def show_call_screening(self, event: dict):
        self._win._call_screening_sig.emit(event or {})

    def update_call_screening_transcript(self, speaker: str, text: str):
        self._win._call_screening_sig.emit({
            "action": "transcript",
            "speaker": speaker,
            "text": text,
        })

    def hide_call_screening(self):
        self._win._call_screening_sig.emit({"action": "hide"})

    def show_attention_alert(self, event: dict):
        self._win._attention_sig.emit(event or {})

    def set_meeting_mode(self, enabled: bool, title: str = "", summary: str = "", answer: str = "", speech: str = ""):
        self._ui(lambda: self._win.set_meeting_mode(enabled, title, summary, answer, speech))

    def begin_task_workspace(self, command: str, plan: list[str] | str | None = None, source: str = "local"):
        self._win._task_workspace_sig.emit({
            "action": "start",
            "command": command or "",
            "plan": plan or [],
            "source": source or "local",
        })

    def capture_screen_bytes(self) -> bytes:
        import threading
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QBuffer, QIODevice, Qt

        if threading.current_thread() == threading.main_thread():
            screen = QApplication.primaryScreen()
            if not screen: return b""
            pixmap = screen.grabWindow(0)
            pixmap = pixmap.scaled(640, 360, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            pixmap.save(buffer, 'JPG', 55)
            return buffer.data().data()
        else:
            return self._win.capture_screen_threadsafe()

    def update_task_workspace(self, *, title: str | None = None, command: str | None = None, plan: list[str] | str | None = None,
                              status: str | None = None, output: str | None = None, percent: int | None = None,
                              footer: str | None = None, source: str | None = None):
        payload = {"action": "update"}
        if title is not None:
            payload["title"] = title
        if command is not None:
            payload["command"] = command
        if plan is not None:
            payload["plan"] = plan
        if status is not None:
            payload["status"] = status
        if output is not None:
            payload["output"] = output
        if percent is not None:
            payload["percent"] = percent
        if footer is not None:
            payload["footer"] = footer
        if source is not None:
            payload["source"] = source or "local"
        self._win._task_workspace_sig.emit(payload)

    def finish_task_workspace(self, result: str, status: str = "Task completed.", percent: int = 100):
        self._win._task_workspace_sig.emit({
            "action": "finish",
            "result": result or "Done.",
            "status": status or "Task completed.",
            "percent": percent,
        })

    def clear_task_workspace(self):
        self._win._task_workspace_sig.emit({"action": "clear"})

    def show_hud_operation(self, title: str, step: str, sources: list = None, tool: str = None, **kwargs):
        """Displays live operation telemetry on the right HUD wing."""
        if hasattr(self, "_win") and hasattr(self._win, "_hud_operation_sig"):
            payload = {
                "title": title,
                "step": step,
                "sources": sources or [],
                "tool": tool or ""
            }
            payload.update(kwargs)
            self._win._hud_operation_sig.emit(payload)

    def show_hud_deliverable(self, title: str, summary: str = "", bullets: list = None,
                             file_path: str = None, kind: str = "result", actions: list = None, data: dict = None, **kwargs):
        """Displays final results or file deliverables on the left HUD wing."""
        if hasattr(self, "_win") and hasattr(self._win, "_hud_deliverable_sig"):
            payload = {
                "title": title,
                "summary": summary or "",
                "bullets": bullets or [],
                "file_path": file_path or "",
                "kind": kind or "result",
                "actions": actions or [],
                "data": data or {}
            }
            payload.update(kwargs)
            self._win._hud_deliverable_sig.emit(payload)

    def show_content(self, title: str, body: str):
        """Universal rich content presenter. Automatically routes to the Brahma Holographic Left Deliverable Wing."""
        import re
        file_path = None
        m = re.search(r'([A-Za-z]:\\[^\s"\'<>`\r\n]+\.(?:pdf|docx|xlsx|pptx|png|jpg|mp4|py|html|json|txt))', body)
        if m:
            cand = Path(m.group(1).strip())
            if cand.exists():
                file_path = str(cand.resolve())
        if not file_path:
            m2 = re.search(r'([^\s"\'<>`\r\n]+\.(?:pdf|docx|xlsx|pptx|png|jpg|mp4|py|html|json|txt))', body)
            if m2:
                cand2 = Path(m2.group(1).strip())
                if cand2.exists():
                    file_path = str(cand2.resolve())

        bullets = []
        for line in body.splitlines():
            line_str = line.strip()
            if (line_str.startswith(("-", "*", "•", ">")) or line_str.startswith(tuple("123456789."))) and len(line_str) > 2:
                clean_bullet = line_str.lstrip("-*•>0123456789. ").strip()
                if clean_bullet and not clean_bullet.startswith("#"):
                    bullets.append(clean_bullet)

        self.show_hud_deliverable(
            title=title,
            summary=body[:220] if not bullets else "",
            bullets=bullets[:5],
            file_path=file_path,
            kind="deliverable"
        )

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        # Every speech completion used to force LISTENING, which stomped the
        # STANDBY indicator right back the moment anything finished speaking.
        if not self.muted and self._win._state != "STANDBY":
            self.set_state("LISTENING")
