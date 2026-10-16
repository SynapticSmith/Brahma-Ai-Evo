"""Attention alerts + meeting-mode flows, lifted out of main.py's BrahmaLive (P4-19 seam).

Behavior-preserving move: BrahmaLive keeps thin delegating wrappers for the six
methods wired as callbacks or called from `_on_text_command`, and hands this
module a host reference, so there is no circular import. The alert/meeting state
(`_pending_attention`, `_attention_lock`, `_meeting_active`, `_meeting_event`,
`_meeting_assistant`, `_meeting_lock`) stays on the host (`self._host.*`) because
other host methods also read and set it.

The moved bodies are byte-identical to the originals apart from the documented
rewrite that rebinds every `self.NAME` attribute reference (except the ten
methods moved here, which drop their leading underscore) to the host.
"""
from __future__ import annotations

import threading

from actions.attention_monitor import handle_call_action, read_event_preview, speak_native


class AttentionFlow:
    """Attention-alert announcements and meeting-mode control for BrahmaLive."""

    def __init__(self, host):
        self._host = host

    def attention_message(self, event: dict) -> str:
        app = (event.get("app") or "an app").strip()
        kind = (event.get("kind") or "message").strip().lower()
        if kind == "call":
            return f"Incoming call detected on {app}. Should I pick it up, ignore it, or cut the call?"
        title = (event.get("title") or "").strip()
        preview = (event.get("preview") or "").strip()
        if title:
            return f"You received a message on {app} from {title}. It says: {preview}"
        return f"You received a message on {app}. It says: {preview}"

    def announce_attention(self, event: dict):
        msg = self.attention_message(event)
        self._host.ui.write_log(f"Brahma Evo: {msg}")
        if self._host.session and self._host._loop:
            self._host.speak(msg)
        else:
            threading.Thread(target=speak_native, args=(msg,), daemon=True).start()
        self._host.ui.show_attention_alert(event)

    def on_external_notification(self, event: dict):
        if not isinstance(event, dict):
            return
        kind = (event.get("kind") or "message").strip().lower()
        app = (event.get("app") or "App").strip()
        preview = (event.get("preview") or "").strip()
        settings = {}
        try:
            settings = self._host.ui._load_app_settings()
        except Exception:
            settings = {}

        if kind == "call" and not bool(settings.get("attention_call_prompts", True)):
            return
        if kind == "message" and not bool(settings.get("attention_message_prompts", True)):
            return

        with self._host._attention_lock:
            current = self._host._pending_attention
            if current:
                same_app = (current.get("app") or "").strip().lower() == app.lower()
                same_kind = (current.get("kind") or "").strip().lower() == kind
                if same_app and same_kind:
                    return
            self._host._pending_attention = dict(event)

        self.announce_attention(event)
        if preview:
            self._host.ui.write_log(f"[Attention] {app}: {preview}")
        if kind == "call" and app.lower() in {"zoom", "teams", "whatsapp"}:
            self.start_meeting_mode(event)

    def start_meeting_mode(self, event: dict):
        event = dict(event or {})
        app = (event.get("app") or "Meeting").strip()
        title = event.get("title") or f"{app} meeting"
        summary = f"Watching {app} for questions and answers."
        with self._host._meeting_lock:
            self._host._meeting_active = True
            self._host._meeting_event = event
        self._host.ui.set_meeting_mode(True, title, summary, "Listening for questions on screen...", self._host._meeting_assistant.latest_speech())
        self._host._meeting_assistant.start(title=title, context=summary)
        self._host.ui.write_log(f"SYS: Meeting mode enabled for {app}.")

    def stop_meeting_mode(self, reason: str = "Meeting mode stopped."):
        with self._host._meeting_lock:
            was_active = self._host._meeting_active
            self._host._meeting_active = False
            self._host._meeting_event = None
        if was_active:
            self._host._meeting_assistant.stop()
            self._host.ui.set_meeting_mode(False, "", "", "")
            self._host.ui.write_log(f"SYS: {reason}")

    def on_meeting_update(self, payload: dict):
        if not isinstance(payload, dict):
            return
        active = bool(payload.get("active"))
        title = payload.get("title") or "Meeting mode"
        summary = payload.get("summary") or ""
        answer = payload.get("answer") or ""
        speech = payload.get("speech") or ""
        self._host.ui.set_meeting_mode(active, title, summary, answer, speech)
        if summary:
            self._host.ui.write_log(f"[Meeting] {summary}")
        if answer:
            self._host.ui.write_log(f"Brahma Evo: {answer}")

    def on_meeting_state(self, state: str):
        if state == "LISTENING":
            self._host.ui.set_state("LISTENING")
        elif state == "MEETING":
            self._host.ui.set_state("THINKING")

    def attention_matches(self, text: str, words: tuple[str, ...]) -> bool:
        t = (text or "").lower()
        return any(word in t for word in words)

    def handle_attention_response(self, text: str) -> bool:
        with self._host._attention_lock:
            event = dict(self._host._pending_attention or {})
        if not event:
            return False

        kind = (event.get("kind") or "message").strip().lower()
        lower = (text or "").lower()

        if kind == "message":
            if self.attention_matches(lower, ("reply", "respond", "answer", "write back", "send reply", "send a reply")):
                return self._host._prompt_message_reply(event)
            if self.attention_matches(lower, ("hear", "read", "what is it", "tell me", "show it", "open it")):
                preview = read_event_preview(event)
                self._host.ui.write_log(f"Brahma Evo: {preview}")
                threading.Thread(target=speak_native, args=(preview,), daemon=True).start()
                with self._host._attention_lock:
                    self._host._pending_attention = None
                return True
            if self.attention_matches(lower, ("ignore", "dismiss", "skip", "no", "not now")):
                self._host.ui.write_log("SYS: Message alert dismissed.")
                with self._host._attention_lock:
                    self._host._pending_attention = None
                return True
            return False

        if kind == "call":
            if self.attention_matches(lower, ("pick up", "answer", "accept", "take it", "join")):
                result = handle_call_action(event, "accept")
                self._host.ui.write_log(f"SYS: {result}")
                threading.Thread(target=speak_native, args=(result,), daemon=True).start()
                with self._host._attention_lock:
                    self._host._pending_attention = None
                return True
            if self.attention_matches(lower, ("ignore", "decline", "reject", "cut", "hang up", "end")):
                result = handle_call_action(event, "decline")
                self._host.ui.write_log(f"SYS: {result}")
                threading.Thread(target=speak_native, args=(result,), daemon=True).start()
                with self._host._attention_lock:
                    self._host._pending_attention = None
                return True
            if self.attention_matches(lower, ("x", "nothing", "do nothing", "close")):
                self._host.ui.write_log("SYS: Call alert dismissed.")
                with self._host._attention_lock:
                    self._host._pending_attention = None
                return True
            return False

        return False

    def on_attention_action(self, event: dict, decision: str):
        if not isinstance(event, dict):
            return
        kind = (event.get("kind") or "message").strip().lower()
        decision = (decision or "").strip().lower()

        if kind == "meeting":
            if decision == "stop":
                self.stop_meeting_mode()
            return

        if kind == "message":
            if decision == "hear":
                preview = read_event_preview(event)
                self._host.ui.write_log(f"Brahma Evo: {preview}")
                threading.Thread(target=speak_native, args=(preview,), daemon=True).start()
            elif decision == "reply":
                self._host._prompt_message_reply(event)
                return
            else:
                self._host.ui.write_log("SYS: Message alert dismissed.")
            with self._host._attention_lock:
                self._host._pending_attention = None
            return

        if kind == "call":
            if decision in {"accept", "answer", "pick_up"}:
                result = handle_call_action(event, "accept")
                self._host.ui.write_log(f"SYS: {result}")
                threading.Thread(target=speak_native, args=(result,), daemon=True).start()
            elif decision in {"noop", "x", "none"}:
                self._host.ui.write_log("SYS: Call alert dismissed.")
            else:
                result = handle_call_action(event, "decline")
                self._host.ui.write_log(f"SYS: {result}")
                threading.Thread(target=speak_native, args=(result,), daemon=True).start()
            with self._host._attention_lock:
                self._host._pending_attention = None
