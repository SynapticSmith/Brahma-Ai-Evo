import threading

from core import ui_overlays_cards


class _Signal:
    def connect(self, callback):
        return None


class _Overlay:
    instances = []

    def __init__(self, *args, **kwargs):
        self.answered = _Signal()
        self.always = _Signal()
        self.closed = _Signal()
        _Overlay.instances.append(self)

    def show(self):
        return None

    def raise_(self):
        return None

    def deleteLater(self):
        return None


class _Window:
    def __init__(self, minimized, visible):
        self._minimized = minimized
        self._visible = visible

    def isMinimized(self):
        return self._minimized

    def isVisible(self):
        return self._visible


class _Host(ui_overlays_cards._OverlayCardsMixin):
    def __init__(self, minimized, visible):
        self._gui_thread_id = threading.get_ident()
        self._win = _Window(minimized, visible)
        self._confirm_overlay = None
        self.restored = 0

    def show_main(self):
        self.restored += 1

    def _position_confirm_overlay(self):
        return None

    def _on_confirm_answered(self, *args):
        return None

    def _on_confirm_always(self, *args):
        return None


def test_confirm_overlay_restores_minimized_window(monkeypatch):
    monkeypatch.setattr(
        ui_overlays_cards, "ConfirmationOverlay", _Overlay, raising=False
    )
    _Overlay.instances.clear()
    host = _Host(minimized=True, visible=False)

    host._apply_confirm_overlay("show", "Approve?", "detail")

    assert host.restored == 1
    assert len(_Overlay.instances) == 1


def test_confirm_overlay_leaves_visible_window_alone(monkeypatch):
    monkeypatch.setattr(
        ui_overlays_cards, "ConfirmationOverlay", _Overlay, raising=False
    )
    _Overlay.instances.clear()
    host = _Host(minimized=False, visible=True)

    host._apply_confirm_overlay("show", "Approve?", "detail")

    assert host.restored == 0
    assert len(_Overlay.instances) == 1
