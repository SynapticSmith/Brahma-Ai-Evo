"""MainWindow's _AvatarAudioMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations



class _AvatarAudioMixin:
    def _attach_avatar_renderer(self):
        """Give core.avatar_runtime somewhere to send an emotion tag.

        The registry never imports a widget - that would invert the dependency
        for one renderer - so the UI offers a sink and the core fills it. Weak
        reference, because the registry outliving the window must not keep the
        window (and its WebGL context) alive.
        """
        try:
            import weakref
            from core import avatar_runtime

            ref = weakref.ref(self)

            def _sink(tag: str, intensity: float) -> bool:
                win = ref()
                if win is None:
                    return False
                bg = getattr(win, "_bg_widget", None)
                fn = getattr(bg, "set_avatar_emotion", None)
                if not callable(fn):
                    return False
                return bool(fn(tag, intensity))

            avatar_runtime.set_webengine_sink(_sink)
        except Exception:
            pass

    def refresh_avatar_renderer(self) -> bool:
        """Re-point the background at whichever avatar document is now selected.

        Called after the backend picker changes, because the two backends are
        separate documents rather than one page with a mode flag.
        """
        try:
            bg = getattr(self, "_bg_widget", None)
            fn = getattr(bg, "refresh_avatar_page", None)
            if not callable(fn):
                return False
            return bool(fn())
        except Exception:
            return False

    def _on_audio_level_sig(self, level: float):
        if hasattr(self, "_bg_widget") and hasattr(self._bg_widget, "set_audio_level"):
            self._bg_widget.set_audio_level(level)
        if hasattr(self, "_chat_workspace") and hasattr(self._chat_workspace, "set_audio_level"):
            self._chat_workspace.set_audio_level(level)
        if hasattr(self, "_command_bar") and hasattr(self._command_bar, "set_audio_level"):
            self._command_bar.set_audio_level(level)

    def set_audio_level(self, level: float):
        try:
            self._audio_level_sig.emit(float(level))
        except Exception:
            pass
