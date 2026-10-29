"""MainWindow's _OverlayCardsMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations



def bind(*, ConfirmationOverlay, DailyBriefingOverlay):
    globals().update(ConfirmationOverlay=ConfirmationOverlay, DailyBriefingOverlay=DailyBriefingOverlay)


class _OverlayCardsMixin:
    def _restart_card_hide_timer(self):
        if hasattr(self, "_card_hide_tmr"):
            self._card_hide_tmr.start(5000)

    def _hide_command_cards(self):
        if hasattr(self, "_command_card"):
            self._command_card.hide()
        if hasattr(self, "_result_card"):
            self._result_card.hide()
        if hasattr(self, "_hud_result_wing"):
            self._hud_result_wing.dismiss()
        if hasattr(self, "_hud_telemetry_wing"):
            self._hud_telemetry_wing.dismiss()

    def _apply_hud_operation(self, data: dict):
        if hasattr(self, "_hud_telemetry_wing"):
            self._hud_telemetry_wing.show_operation(
                title=data.get("title") or "OPERATION IN PROGRESS",
                step=data.get("step") or "Processing...",
                sources=data.get("sources") or [],
                tool=data.get("tool") or ""
            )

    def _apply_hud_deliverable(self, data: dict):
        if hasattr(self, "_hud_result_wing"):
            self._hud_result_wing.show_deliverable(
                title=data.get("title") or "MISSION DELIVERABLE",
                summary=data.get("summary") or "",
                bullets=data.get("bullets") or [],
                file_path=data.get("file_path"),
                kind=data.get("kind") or "result",
                actions=data.get("actions") or [],
                data=data.get("data") or {}
            )

    def _apply_daily_briefing(self, payload):
        action, data = payload if isinstance(payload, tuple) else ("show", payload)
        if action == "hide":
            if getattr(self, "_briefing_overlay", None):
                try:
                    self._briefing_overlay.hide()
                    self._briefing_overlay.deleteLater()
                except Exception:
                    pass
                self._briefing_overlay = None
            if hasattr(self, "_briefing_card"):
                self._briefing_card.hide()
            if hasattr(self, "_smart_devices_section"):
                self._smart_devices_section.show()
            return

        # Legacy label support if present
        if hasattr(self, "_briefing_text_lbl"):
            summary_txt = data if isinstance(data, str) else (data.get("spoken_narrative", "") if isinstance(data, dict) else str(data))
            clean_text = " ".join(str(summary_txt or "").split())
            self._briefing_text_lbl.setText(clean_text[:360] + ("..." if len(clean_text) > 360 else ""))

        # Holographic HUD card overlay
        if getattr(self, "_briefing_overlay", None):
            try:
                self._briefing_overlay.deleteLater()
            except Exception:
                pass
            self._briefing_overlay = None

        overlay = DailyBriefingOverlay(data, parent=self)
        overlay.closed.connect(lambda: setattr(self, "_briefing_overlay", None))
        self._briefing_overlay = overlay
        self._position_briefing_overlay()
        overlay.show()
        overlay.raise_()

        try:
            from sound_manager import sound_mgr
            sound_mgr.play_sfx("telemetry_chirp")
        except Exception:
            pass

    def _position_briefing_overlay(self):
        if not getattr(self, "_briefing_overlay", None):
            return
        geo = self.geometry()
        x = max(16, (geo.width() - self._briefing_overlay.width()) // 2)
        y = max(16, (geo.height() - self._briefing_overlay.height()) // 2)
        self._briefing_overlay.move(x, y)

    def _hide_daily_briefing_card(self):
        self._apply_daily_briefing(("hide", None))

    def _schedule_daily_briefing_hide(self):
        if getattr(self, "_briefing_overlay", None):
            self._briefing_overlay._remaining_secs = min(getattr(self._briefing_overlay, "_remaining_secs", 30), 10)

    def show_daily_briefing(self, text):
        self._briefing_sig.emit(("show", text))

    def hide_daily_briefing(self):
        self._briefing_sig.emit(("hide", None))

    def schedule_daily_briefing_hide(self):
        self._schedule_daily_briefing_hide()

    def show_confirm(self, title: str, detail: str = ""):
        self._confirm_sig.emit("show", title, detail)

    def hide_confirm(self):
        self._confirm_sig.emit("hide", "", "")

    def _apply_confirm_overlay(self, action: str, title: str = "", detail: str = ""):
        if action == "hide":
            if getattr(self, "_confirm_overlay", None):
                try:
                    self._confirm_overlay.deleteLater()
                except Exception:
                    pass
                self._confirm_overlay = None
            return

        if action == "show":
            if getattr(self, "_confirm_overlay", None):
                try:
                    self._confirm_overlay.deleteLater()
                except Exception:
                    pass
                self._confirm_overlay = None

            allow_always = False
            try:
                from core import confirm as confirm_gate

                allow_always = confirm_gate.pending_allows_always()
            except Exception:
                pass
            overlay = ConfirmationOverlay(
                title, detail, parent=self, allow_always=allow_always
            )
            overlay.answered.connect(self._on_confirm_answered)
            overlay.always.connect(self._on_confirm_always)
            overlay.closed.connect(lambda: setattr(self, "_confirm_overlay", None))
            self._confirm_overlay = overlay
            self._position_confirm_overlay()
            overlay.show()
            overlay.raise_()

            try:
                from sound_manager import sound_mgr
                sound_mgr.play_sfx("telemetry_chirp")
            except Exception:
                pass
