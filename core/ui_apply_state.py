"""MainWindow's _ApplyStateMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

from core.ui_theme import (C)


class _ApplyStateMixin:
    def _apply_state(self, state: str):
        self._state = state
        if hasattr(self, "_bg_widget") and hasattr(self._bg_widget, "set_ai_state"):
            self._bg_widget.set_ai_state(state)
        if hasattr(self, "_chat_workspace") and hasattr(self._chat_workspace, "set_state"):
            self._chat_workspace.set_state(state)
        if hasattr(self, "_command_bar") and hasattr(self._command_bar, "set_state"):
            self._command_bar.set_state(state)
        elif hasattr(self, "centralWidget") and hasattr(self.centralWidget(), "set_ai_state"):
            self.centralWidget().set_ai_state(state)

        # Update top taskbar status badge
        if hasattr(self, "_status_badge"):
            badge_map = {
                "LISTENING":  ("◉ LISTENING",  "#00e5ff", "rgba(0, 229, 255, 0.4)",  "rgba(0, 229, 255, 0.08)"),
                "SPEAKING":   ("◈ SPEAKING",   "#00e5ff", "rgba(0, 229, 255, 0.5)",  "rgba(0, 229, 255, 0.12)"),
                "THINKING":   ("◒ THINKING",   "#ff9100", "rgba(255, 145, 0, 0.45)", "rgba(255, 145, 0, 0.08)"),
                "PROCESSING": ("◒ PROCESSING", "#ff9100", "rgba(255, 145, 0, 0.45)", "rgba(255, 145, 0, 0.08)"),
                "EXECUTING":  ("⚡ EXECUTING",  "#7c4dff", "rgba(124, 77, 255, 0.45)","rgba(124, 77, 255, 0.08)"),
                "WORKING":    ("⚡ WORKING",    "#7c4dff", "rgba(124, 77, 255, 0.45)","rgba(124, 77, 255, 0.08)"),
                "MUTED":      ("⊗ MUTED",      "#ff3b30", "rgba(255, 59, 48, 0.45)", "rgba(255, 59, 48, 0.08)"),
                "SCANNING":   ("◓ SCANNING",   "#37ff5f", "rgba(55, 255, 95, 0.45)", "rgba(55, 255, 95, 0.08)"),
                "STANDBY":    ("◌ STANDBY",    "#6e7a8a", "rgba(110, 122, 138, 0.40)","rgba(110, 122, 138, 0.08)"),
            }
            lbl_txt, col, border, bg = badge_map.get(state, ("● ONLINE", "#00e5ff", "rgba(0, 229, 255, 0.25)", "rgba(12,14,18,220)"))
            self._status_badge.setText(lbl_txt)
            self._status_badge.setStyleSheet(
                f"color: {col}; background: {bg}; border: 1px solid {border}; border-radius: 8px; padding: 5px 12px;"
            )

        # Update command row input placeholder
        if hasattr(self, "_input"):
            ph_map = {
                "LISTENING": "Listening... (or type your command)",
                "SPEAKING": "Brahma Evo is responding...",
                "THINKING": "Brahma is thinking...",
                "PROCESSING": "Processing request...",
                "EXECUTING": "Executing action...",
                "WORKING": "Working on it...",
                "MUTED": "Microphone muted — type command here...",
                "SCANNING": "Scanning display...",
                "STANDBY": "Standby - say \"Brahma Evo\" or press Connect...",
            }
            self._input.setPlaceholderText(ph_map.get(state, "Ask Brahma Evo anything..."))

        # Update chat workspace footer status
        if hasattr(self, "_inline_workspace") and hasattr(self._inline_workspace, "_footer_status"):
            foot_map = {
                "LISTENING": "● Listening for voice command...",
                "SPEAKING": "● Brahma is speaking...",
                "THINKING": "● Thinking...",
                "PROCESSING": "● Processing...",
                "EXECUTING": "● Executing system command...",
                "WORKING": "● Working on task...",
                "MUTED": "● Voice input muted",
                "SCANNING": "● Vision system active",
                "STANDBY": "● Standby - no session (say \"Brahma Evo\" or press Connect)",
            }
            self._inline_workspace._footer_status.setText(foot_map.get(state, "Brahma Evo is online"))

        if hasattr(self, "_status_chip"):
            chip_text = {
                "THINKING": "● WORKING",
                "SPEAKING": "● SPEAKING",
                "MUTED":    "● MUTED",
                "STANDBY": "● STANDBY",
            }.get(state, "● ONLINE")
            self._status_chip.setText(chip_text)
            color = C.PRI if state == "MUTED" else "#6e7a8a" if state == "STANDBY" else C.GREEN if state in ("LISTENING", "SPEAKING") else C.WHITE
            border = C.PRI if state in ("MUTED", "THINKING") else "rgba(255,255,255,0.12)"
            self._status_chip.setStyleSheet(
                f"color: {color}; background: rgba(11,12,16,238); border: 1px solid {border}; border-radius: 999px; padding: 7px 14px;"
            )
        if hasattr(self, "_time_status_lbl"):
            status_text = {
                "THINKING": "Working",
                "SPEAKING": "Speaking",
                "MUTED": "Muted",
                "STANDBY": "On Call",
            }.get(state, "Online")
            self._time_status_lbl.setText(status_text)
            self._time_status_lbl.setStyleSheet(
                f"color: {C.GREEN if state in ('LISTENING', 'SPEAKING') else C.RED if state == 'MUTED' else '#6e7a8a' if state == 'STANDBY' else C.WHITE}; background: transparent;"
            )
        if hasattr(self, "_task_card"):
            if state in ("THINKING", "PROCESSING", "EXECUTING", "WORKING"):
                self._task_card.set_task("Working on it...", "Brahma Evo is processing your request.", 72)
            elif state == "SPEAKING":
                self._task_card.set_task("Responding...", "Brahma Evo is speaking now.", 100)
            elif state == "MUTED":
                self._task_card.set_task("Microphone muted", "Voice input is paused.", 0)
            elif state == "STANDBY":
                self._task_card.set_task(
                    "On Call standby",
                    'Socket closed. Say "Brahma Evo" or press Connect.', 0
                )
            else:
                self._task_card.set_task("Ready", "Brahma Evo is idle and ready.", 0)
        if hasattr(self, "_result_card"):
            if state in ("THINKING", "PROCESSING", "EXECUTING", "WORKING"):
                self._result_card.set_body("Action pending")
            elif state == "SPEAKING":
                self._result_card.set_body("Speaking now")
            elif state == "MUTED":
                self._result_card.set_body("Voice muted")
            elif state == "STANDBY":
                self._result_card.set_body("Waiting for wake word")
            else:
                self._result_card.set_body("Action completed")
