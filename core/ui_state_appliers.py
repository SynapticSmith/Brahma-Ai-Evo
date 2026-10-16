"""MainWindow's _StateAppliersMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

import json



def bind(*, API_FILE, MeetingOverlay, ScanningOverlay):
    globals().update(API_FILE=API_FILE, MeetingOverlay=MeetingOverlay, ScanningOverlay=ScanningOverlay)


class _StateAppliersMixin:
    def set_voice_status(self, backend: str = "", live: bool | None = None,
                          detail: str = ""):
        """Report which voice backend is driving the session, and whether it is up.

        The chat/footer indicator only ever reflected the microphone, so a local
        voice session, a Gemini Live socket and a custom endpoint looked
        identical. Connectivity is now stated explicitly and is independent of
        which backend is selected.
        """
        for holder_name in ("_chat_workspace", "_command_bar"):
            holder = getattr(self, holder_name, None)
            if holder is not None and hasattr(holder, "set_voice_status"):
                try:
                    holder.set_voice_status(backend, live, detail)
                except Exception:
                    pass
        # Keep a copy on the window so any later indicator can read it too.
        if backend:
            self._voice_backend = str(backend).strip()
        if live is not None:
            self._voice_live = bool(live)
        if detail:
            self._voice_detail = str(detail).strip()
    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return (bool(d.get("gemini_api_key")) and
                    bool(d.get("os_system")))
        except Exception:
            return False

    def _apply_scan_state(self, enabled: bool, text: str = ""):
        if enabled:
            if self._scan_overlay is None:
                self._scan_overlay = ScanningOverlay()
            self._scan_overlay.show_fullscreen(text or "SCANNING SCREEN", "Analyzing display...")
        else:
            if self._scan_overlay is not None:
                self._scan_overlay.hide_overlay()

    def set_scanning(self, enabled: bool, text: str = ""):
        self._scan_sig.emit(bool(enabled), text or "")

    def set_meeting_mode(self, enabled: bool, title: str = "", summary: str = "", answer: str = "", speech: str = ""):
        self._meeting_sig.emit({
            "enabled": bool(enabled),
            "title": title or "",
            "summary": summary or "",
            "answer": answer or "",
            "speech": speech or "",
        })

    def _on_screen_capture_requested(self):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QBuffer, QIODevice, Qt
        try:
            screen = QApplication.primaryScreen()
            if not screen:
                self._screen_capture_result = b""
            else:
                pixmap = screen.grabWindow(0)
                pixmap = pixmap.scaled(640, 360, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                pixmap.save(buffer, 'JPG', 55)
                self._screen_capture_result = buffer.data().data()
        except Exception:
            self._screen_capture_result = b""
        finally:
            self._screen_capture_event.set()

    def capture_screen_threadsafe(self) -> bytes:
        print("[UI] capture_screen_threadsafe started")
        self._screen_capture_event.clear()
        self._screen_capture_sig.emit()
        self._screen_capture_event.wait(timeout=5.0)
        print(f"[UI] capture_screen_threadsafe finished, result {len(self._screen_capture_result) if self._screen_capture_result is not None else 'None'} bytes")
        return self._screen_capture_result

    def _apply_meeting_state(self, event: object):
        data = event if isinstance(event, dict) else {}
        enabled = bool(data.get("enabled"))
        title = (data.get("title") or "").strip()
        summary = (data.get("summary") or "").strip()
        answer = (data.get("answer") or "").strip()
        speech = (data.get("speech") or "").strip()

        if enabled:
            if self._meeting_overlay is None:
                self._meeting_overlay = MeetingOverlay()
                self._meeting_overlay.stop_requested.connect(self._request_stop_meeting)
                self._meeting_overlay.minimize_requested.connect(self._toggle_meeting_overlay)
                self._meeting_overlay.close_requested.connect(self._request_stop_meeting)
            self._meeting_overlay.set_content(
                title or "Meeting mode",
                summary or "Watching the meeting screen.",
                answer or "No question detected yet.",
                True,
                speech,
            )
            self._position_meeting_overlay()
            self._meeting_overlay.set_collapsed(self._meeting_overlay_collapsed)
            self._meeting_overlay.show()

    def _apply_task_workspace(self, event: object):
        data = event if isinstance(event, dict) else {}
        target = getattr(self, "_task_card", None)
        if target is None:
            return
        action = (data.get("action") or "update").strip().lower()
        if action == "start":
            target.start_workspace(
                data.get("command") or "",
                data.get("plan") or [],
                data.get("source") or "local",
            )
        elif action == "update":
            target.update_workspace(
                title=data.get("title"),
                command=data.get("command"),
                plan=data.get("plan"),
                status=data.get("status"),
                output=data.get("output"),
                percent=data.get("percent"),
                footer=data.get("footer"),
            )
        elif action == "finish":
            target.finish_workspace(
                data.get("result") or data.get("output") or "Done.",
                status=data.get("status") or "Task completed.",
                percent=int(data.get("percent") or 100),
            )
        elif action == "clear":
            target.clear_workspace()
