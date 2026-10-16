"""Reply / Instagram / email flows, lifted out of main.py's BrahmaLive (P4-19 seam).

Behavior-preserving move: BrahmaLive keeps thin delegating wrappers and hands this
module a host reference plus the three main.py symbols it needs
(`_gemini_text_reply`, `_get_api_key`, `_ig_gemini_reply`), so there is no circular
import. The reply/email/IG-mode state flags stay on the host (`self._host.*`) because
`_on_text_command` also reads and sets them.
"""
from __future__ import annotations

import json
import threading

from google import genai

from llm_client import client as openrouter_client
from actions.attention_monitor import speak_native
from actions.send_message import send_message


class ReplyFlow:
    """Pending-reply, Instagram-reply and email-compose flows for BrahmaLive."""

    def __init__(self, host, *, gemini_text_reply, get_api_key, ig_gemini_reply):
        self._host = host
        self._gemini_text_reply = gemini_text_reply
        self._get_api_key = get_api_key
        self._ig_gemini_reply = ig_gemini_reply

    def prompt_message_reply(self, event: dict) -> bool:
        if not isinstance(event, dict):
            return False
        with self._host._attention_lock:
            self._host._pending_reply_event = dict(event)
            self._host._pending_attention = None
            self._host._reply_mode = True

        message = "What would you like to say in reply?"
        self._host.ui.write_log(f"Brahma Evo: {message}")
        if self._host.session and self._host._loop:
            self._host.speak(message)
        else:
            threading.Thread(target=speak_native, args=(message,), daemon=True).start()
        try:
            self._host.ui.begin_task_workspace(
                "Replying to message",
                [
                    "Type your response",
                    "I will reword it naturally",
                    f"Send via {event.get('app', 'the app')}",
                ],
                source="reply",
            )
            self._host.ui.update_task_workspace(
                status="Awaiting your reply",
                output="Type the message you want to send, and I will make it sound natural before sending it as you.",
                percent=10,
            )
        except Exception:
            pass
        return True

    def handle_pending_reply(self, text: str) -> bool:
        with self._host._attention_lock:
            event = dict(self._host._pending_reply_event or {})
            self._host._pending_reply_event = None
        if not event:
            return False

        lower = (text or "").lower()
        if self._host._attention_matches(lower, ("cancel", "never mind", "skip", "do not send", "don't send")):
            self._host._reply_mode = False
            self._host.ui.write_log("SYS: Reply cancelled.")
            try:
                self._host.ui.finish_task_workspace("Reply cancelled.", "Cancelled", 100)
            except Exception:
                pass
            return True

        self._host.ui.write_log(f"SYS: Drafting reply to {event.get('title') or event.get('app')}.")
        threading.Thread(target=self.draft_and_send_reply, args=(event, text), daemon=True).start()
        return True

    def rewrite_reply_text(self, user_text: str, event: dict) -> str:
        prompt = (
            "You are a friendly assistant helping a user rewrite their draft reply for a chat message. "
            "Keep the same meaning and intent, expand the wording slightly, and make it sound natural and human. "
            "Do not mention the notification, app, or any internal system details. "
            "Return only the rewritten reply text.\n\n"
            "Notification context:\n"
            f"App: {event.get('app', '')}\n"
            f"Sender: {event.get('title', '')}\n"
            f"Preview: {event.get('preview', '')}\n\n"
            "User draft reply:\n"
            f"{user_text}\n\n"
            "Reply text:"
        )
        try:
            return self._gemini_text_reply(prompt) or user_text
        except Exception:
            try:
                return openrouter_client.chat(
                    prompt,
                    system="You are a friendly assistant. Rewrite the reply naturally and humanely.",
                )
            except Exception:
                return user_text

    def draft_and_send_reply(self, event: dict, text: str):
        try:
            reply_text = self.rewrite_reply_text(text, event)
            if not reply_text:
                reply_text = text
            self._host.ui.update_task_workspace(
                status="Sending reply",
                output="Sending your expanded reply now...",
                percent=70,
            )
            receiver = (event.get("title") or "").strip()
            platform = (event.get("app") or "whatsapp").strip()
            if not receiver:
                self._host.ui.write_log("ERR: Could not determine recipient for reply.")
                try:
                    self._host.ui.finish_task_workspace("Reply failed: recipient not found.", "Reply failed", 100)
                except Exception:
                    pass
                return
            result = send_message(
                parameters={
                    "receiver": receiver,
                    "message_text": reply_text,
                    "platform": platform,
                },
                player=self._host.ui,
            )
            self._host._reply_mode = False
            self._host.ui.write_log(f"SYS: {result}")
            try:
                self._host.ui.finish_task_workspace(reply_text, "Reply delivered.", 100)
            except Exception:
                pass
        except Exception as e:
            self._host._reply_mode = False
            self._host.ui.write_log(f"ERR: Reply failed: {e}")
            try:
                self._host.ui.finish_task_workspace(f"Reply failed: {e}", "Reply failed", 100)
            except Exception:
                pass
        finally:
            if not self._host.ui.muted:
                self._host.ui.set_state("LISTENING")

    def parse_ig_reply_intent(self, text: str) -> tuple[str, str]:
        system_prompt = (
            "You are an intent parser. The user received an Instagram DM. I asked: 'What should I reply, or should I take over?'. "
            "The user responded. Determine their intent.\n"
            "1. If they want me to take over/handle it, return TAKE_OVER.\n"
            "2. If they want to cancel/skip, return CANCEL.\n"
            "3. If they dictate a specific message to send (e.g. 'tell them I am busy', 'say hi'), return MANUAL_REPLY and the exact text.\n"
            "4. If they are just greeting me (e.g. 'hi') or making small talk, return IGNORE.\n"
            "Output ONLY valid JSON: {\"intent\": \"...\", \"reply_text\": \"...\"}"
        )
        try:
            client = genai.Client(api_key=self._get_api_key(), http_options={"api_version": "v1beta"})
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"{system_prompt}\n\nUser Response: {text}",
                config={"temperature": 0.1, "response_mime_type": "application/json"}
            )
            data = json.loads(response.text.strip())
            return data.get("intent", "IGNORE"), data.get("reply_text", "")
        except Exception:
            lower = text.lower()
            if any(c in lower for c in ("cancel", "stop", "skip", "never mind", "abort", "don't reply", "do not reply")):
                return "CANCEL", ""
            if any(a in lower for a in ("take over", "auto mode", "handle it", "you reply", "auto reply", "take care of it")):
                return "TAKE_OVER", ""
            for verb in ("reply", "tell him", "tell her", "tell them", "tell", "say", "send"):
                if verb in lower:
                    import re
                    pattern = rf"^.*?\b{verb}\b(?:\s+(?:him|her|them|that|to\s+say|saying))?\s*"
                    cleaned = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
                    if cleaned:
                        return "MANUAL_REPLY", cleaned
            if len(text.strip().split()) >= 2 and not any(g in lower for g in ("hello", "hey brahma", "who are you")):
                return "MANUAL_REPLY", text.strip()
            return "IGNORE", ""

    def handle_ig_reply_flow(self, text: str) -> bool:
        if getattr(self, "_ig_pending_thread", None):
            thread_id = self._host._ig_pending_thread.get("thread_id")
            username = self._host._ig_pending_thread.get("username")
            message_text = self._host._ig_pending_thread.get('message')
            
            intent, payload = self.parse_ig_reply_intent(text)
            
            if intent == "CANCEL":
                self._host._ig_reply_mode = False
                msg = "Instagram reply cancelled."
                self._host.ui.write_log(f"Brahma Evo: {msg}")
                self._host.speak(msg)
                self._host._ig_pending_thread = None
                return True
                
            if intent == "TAKE_OVER":
                self._host.ui.write_log("SYS: Taking over Instagram thread.")
                self._host.speak(f"I will now take over the chat with {username}.")
                from actions.instagram_mcp import add_auto_thread, send_direct_reply
                add_auto_thread(thread_id)
                def _generate_and_send():
                    try:
                        reply = self._ig_gemini_reply(username, message_text)
                        send_direct_reply(thread_id, reply)
                    except Exception as e:
                        print(f"Error taking over thread: {e}")
                threading.Thread(target=_generate_and_send, daemon=True).start()
                
            elif intent == "MANUAL_REPLY":
                self._host.ui.write_log(f"SYS: Sending manual reply to @{username}: '{payload}'")
                self._host.speak("Message sent.")
                from actions.instagram_mcp import send_direct_reply
                def _do_send():
                    try:
                        send_direct_reply(thread_id, payload)
                    except Exception as e:
                        print(f"Error sending manual reply to @{username}: {e}")
                threading.Thread(target=_do_send, daemon=True).start()
                
            elif intent == "IGNORE":
                return False
                
            self._host._ig_reply_mode = False
            self._host._ig_pending_thread = None
            return True
        return False

    def handle_email_flow(self, text: str) -> bool:
        lower = text.lower()
        if any(cancel in lower for cancel in ("cancel", "never mind", "skip", "stop", "abort")):
            self._host._email_mode = False
            self._host._email_step = 0
            self._host._email_profiles = {}
            msg = "Email sending cancelled, sir."
            self._host.ui.write_log(f"Brahma Evo: {msg}")
            self._host.speak(msg)
            try:
                self._host.ui.finish_task_workspace("Email sending cancelled.", "Cancelled", 100)
            except Exception:
                pass
            return True

        if self._host._email_step == 0:
            # We just collected the recipient
            self._host._email_recipient = text.strip()
            self._host._email_step = 1
            prompt = "Which email app would you like to use? (Gmail, default mail app, etc.)"
            self._host.ui.write_log(f"Brahma Evo: {prompt}")
            self._host.speak(prompt)
            try:
                self._host.ui.update_task_workspace(
                    status="Selecting email app",
                    output=f"Recipient: {self._host._email_recipient}. Asking for email application...",
                    percent=40,
                )
            except Exception:
                pass
            return True

        elif self._host._email_step == 1:
            # We just collected the app name
            self._host._email_app = text.strip()
            self._host._email_step = 2
            
            prompt = "What is the message you'd like to send?"
            self._host.ui.write_log(f"Brahma Evo: {prompt}")
            self._host.speak(prompt)
            try:
                self._host.ui.update_task_workspace(
                    status="Collecting message",
                    output=f"Recipient: {self._host._email_recipient} | App: {self._host._email_app}. Asking for message content...",
                    percent=70,
                )
            except Exception:
                pass
            return True

        elif self._host._email_step == 2:
            # We just collected the message
            self._host._email_message = text.strip()
            self._host._email_mode = False
            self._host._email_step = 0
            
            # Now let's execute composing!
            msg = f"Opening {self._host._email_app} and composing email to {self._host._email_recipient}..."
            self._host.ui.write_log(f"Brahma Evo: {msg}")
            self._host.speak(msg)
            try:
                self._host.ui.update_task_workspace(
                    status="Composing email",
                    output=f"Composing message to {self._host._email_recipient} via {self._host._email_app}...",
                    percent=90,
                )
            except Exception:
                pass
            
            try:
                import urllib.parse
                import webbrowser
                subject = "Message from Brahma Evo"
                quoted_recipient = urllib.parse.quote(self._host._email_recipient)
                quoted_subject = urllib.parse.quote(subject)
                quoted_body = urllib.parse.quote(self._host._email_message)
                
                import os
                import subprocess
                import shutil

                app_lower = self._host._email_app.lower()
                chrome_opened = False

                if "gmail" in app_lower or "chrome" in app_lower:
                    import urllib.parse
                    quoted_recipient = urllib.parse.quote(self._host._email_recipient)
                    quoted_subject = urllib.parse.quote("Message from Brahma Evo")
                    quoted_body = urllib.parse.quote(self._host._email_message)
                    url = f"https://mail.google.com/mail/?view=cm&fs=1&to={quoted_recipient}&su={quoted_subject}&body={quoted_body}"
                    
                    # Use webbrowser to naturally open a new tab in the already running Chrome window
                    import webbrowser
                    webbrowser.open(url)

                elif "outlook" in app_lower:
                    url = f"https://outlook.live.com/default/?path=/mail/action/compose&to={quoted_recipient}&subject={quoted_subject}&body={quoted_body}"
                    webbrowser.open(url)
                else:
                    url = f"mailto:{quoted_recipient}?subject={quoted_subject}&body={quoted_body}"
                    webbrowser.open(url)
                
                try:
                    self._host.ui.finish_task_workspace("Email composed successfully.", "Composed", 100)
                except Exception:
                    pass
            except Exception as e:
                err_msg = f"Failed to compose email: {e}"
                self._host.ui.write_log(f"ERR: {err_msg}")
                self._host.speak(err_msg)
                try:
                    self._host.ui.finish_task_workspace(err_msg, "Failed", 100)
                except Exception:
                    pass
            return True

        return False
