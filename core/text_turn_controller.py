"""Text and agentic turn controller lifted out of main.py's BrahmaLive (ADR-019 wave 3 / B7).

Owns the text command router (``on_text_command``), slash session commands
(``/sessions``, ``/resume``, ``/new``), durable session seeding and voice turn
archival, MCP tool merging, the OpenAI-compatible ``AgentLoop`` driver
(``finish_tool_turn``, ``local_brain_reply``, ``custom_brain_turn``), and the
multi-provider ``fallback_reply`` ladder.

``BrahmaLive`` keeps thin delegating methods so external callers and test
harnesses (including tests that pass a ``types.SimpleNamespace`` or
``object.__new__(BrahmaLive)`` as ``self``) continue to work unchanged.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import threading
from typing import Any

from core import desktop_tools
from core import routing
from core import tool_bridge
from core.local_brain import local_brain
from llm_client import client as openrouter_client
from memory import config_manager


def _main() -> Any:
    """Return the loaded ``main`` module so ``unittest.mock.patch('main.*')`` works."""
    return sys.modules.get("main") or sys.modules.get("__main__")


# Fixed phrase lists for the remaining syntax claims. They are module constants
# so the routing-authority tests can prove each one declines on ordinary text
# (docs/ROUTING-AUDIT.md §2, Phase 4). Matching is word-bounded, never a raw
# substring: "backup" must not contain "ac", "microphone" must not contain
# "phone".
OFFLINE_PHRASES = (
    "switch to offline mode",
    "go offline",
    "turn on offline mode",
    "enable offline mode",
    "air gap mode",
)
ONLINE_PHRASES = (
    "switch to online mode",
    "go online",
    "turn off offline mode",
    "disable offline mode",
)
EMAIL_CHECK_PHRASES = (
    "check email",
    "check my email",
    "unread email",
    "read email",
    "my inbox",
    "check inbox",
    "show emails",
)
CALENDAR_CHECK_PHRASES = (
    "my schedule",
    "what's on my calendar",
    "check calendar",
    "my meetings",
    "schedule today",
    "events today",
)
RAM_CHECK_PHRASES = (
    "eating my ram",
    "ram hogs",
    "memory hogs",
    "check ram",
    "ram usage",
    "memory usage",
    "who is using ram",
)
BATTERY_CHECK_PHRASES = (
    "battery health",
    "check battery",
    "battery status",
    "battery percentage",
    "is laptop charging",
    "how much battery",
)
BRIGHTNESS_PHRASES = (
    "dim screen",
    "dim monitor",
    "increase brightness",
    "lower brightness",
    "set brightness",
    "screen brightness",
)
DIAGNOSTIC_PHRASES = (
    "system status",
    "system diagnostics",
    "hardware status",
    "computer vitals",
    "pc diagnostics",
)
BUG_TRIGGER_PHRASES = (
    "trigger test bug",
    "simulate bug",
    "test bug",
    "create bug",
    "simulate error",
    "trigger error",
    "break test",
)
HEAL_PHRASES = (
    "fix that bug",
    "fix the bug",
    "heal yourself",
    "auto heal",
    "patch yourself",
    "fix error",
    "fix this error",
)
ROLLBACK_PHRASES = ("undo last patch", "rollback patch", "revert patch", "undo patch")
PATCH_HISTORY_PHRASES = ("patch history", "patch log", "show patches", "auto heal status")
VOLUME_PHRASES = (
    "volume up",
    "volume down",
    "mute volume",
    "mute audio",
    "unmute",
    "set volume",
    "increase volume",
    "decrease volume",
    "lower volume",
)
MOBILE_TARGET_PHRASES = ("on phone", "on my phone", "on mobile", "on my mobile", "on android", "on tablet")
OPEN_APP_PREFIXES = (
    "open app ",
    "open ",
    "launch app ",
    "launch ",
    "start app ",
    "start ",
)
#: The software nouns that make a "write/build" request a Brahma Dev request.
#: A bare "script" is ambiguous ("write a script for a play"), so it needs
#: explicit code evidence in addition.
SOFTWARE_NOUNS = (
    "app",
    "website",
    "web",
    "program",
    "project",
    "game",
    "calc",
    "html",
    "react",
)
SCRIPT_EVIDENCE = ("python", "bash", "powershell", "automation", "code")


def looks_like_software_request(text: str) -> bool:
    """A code/build claim needs a software noun, not just an action verb."""
    if routing.any_phrase(text, SOFTWARE_NOUNS):
        return True
    return routing.has_phrase(text, "script") and routing.any_phrase(
        text, SCRIPT_EVIDENCE
    )


class TextTurnController:
    """Stateless controller bound to a ``BrahmaLive`` (or test stub) host."""

    def __init__(self, host: Any) -> None:
        self._host = host

    def handle_agent_session_command(self, text: str) -> bool:
        """Handle ``/sessions``, ``/resume`` and ``/new`` for the text path."""
        host = self._host
        parts = (text or "").strip().split()
        if not parts:
            return False
        command = parts[0].lower()
        if command not in ("/sessions", "/resume", "/new"):
            return False

        from agent import session_bridge as bridge
        from agent.session import AgentSession, SessionNotFound

        if command == "/new":
            host._agent_session = None
            host.ui.write_log("SYS: New agent session. The next message starts fresh.")
            return True

        if command == "/sessions":
            records = AgentSession.recent(20)
            if not records:
                host.ui.write_log("SYS: No agent sessions yet.")
                return True
            host.ui.write_log("SYS: Recent agent sessions (resume with /resume <id>):")
            for record in records:
                host.ui.write_log(f"SYS: {bridge.session_summary(record)}")
            return True

        wanted = parts[1] if len(parts) > 1 else ""
        if wanted and len(wanted) < 32:
            matches = [
                record.id
                for record in AgentSession.recent(200)
                if record.id.startswith(wanted)
            ]
            if len(matches) == 1:
                wanted = matches[0]
            elif len(matches) > 1:
                host.ui.write_log(
                    f"ERR: session prefix '{wanted}' is ambiguous; use the full id."
                )
                return True
        if not wanted:
            records = AgentSession.recent(20)
            if not records:
                host.ui.write_log("SYS: No agent sessions to resume.")
                return True
            labels = [bridge.picker_label(record) for record in records]
            index = -1
            chooser = getattr(host.ui, "choose_session", None)
            if callable(chooser):
                try:
                    index = int(chooser(labels))
                except Exception:
                    index = -1
            if not (0 <= index < len(records)):
                return True
            wanted = records[index].id

        try:
            session = AgentSession.open(wanted)
        except SessionNotFound as exc:
            host.ui.write_log(f"ERR: {exc}")
            return True

        host._agent_session = session
        replay = bridge.transcript(session.messages())
        renderer = getattr(host.ui, "replay_agent_session", None)
        if callable(renderer) and replay:
            try:
                renderer(replay)
            except Exception:
                pass
        host.ui.write_log(
            f"SYS: Resumed agent session {session.id} "
            f"({bridge.title_for(session.title or 'untitled')})."
        )
        return True

    def on_text_command(self, text: str, source: str = "local") -> None:
        host = self._host
        m = _main()
        thread_cls = getattr(getattr(m, "threading", threading), "Thread", threading.Thread)
        host._reset_idle_activity()
        text = (text or "").strip()
        if not text:
            return
        if host._handle_agent_session_command(text):
            return
        # Retrieval context for a compressed search_tools intent. Memory only;
        # the bridge never persists or logs this (TOOL-RETRIEVAL-DESIGN §4.1).
        tool_bridge.set_turn_context(text)
        if len(text) > 4:
            update_mem = getattr(m, "_update_memory_async", None)
            if callable(update_mem):
                thread_cls(target=update_mem, args=(text, ""), daemon=True).start()
        host.ui.set_state("THINKING")
        try:
            stop_speech = getattr(m, "stop_native_speech", None)
            if callable(stop_speech):
                stop_speech()
        except Exception:
            pass
        try:
            pm = getattr(host, "plugin_manager", None)
            if pm is not None:
                if pm.dispatch("on_text_command", text, source):
                    return
        except Exception:
            pass
        if getattr(host, "_reply_mode", False):
            if host._handle_pending_reply(text):
                return
            host._reply_mode = False

        if getattr(host, "_ig_reply_mode", False):
            if host._handle_ig_reply_flow(text):
                return

        if getattr(host, "_email_mode", False):
            if host._handle_email_flow(text):
                return

        lower = text.lower().strip()
        if routing.any_phrase(lower, OFFLINE_PHRASES):
            config_manager.set_setting("offline_mode_enabled", True)
            config_manager.set_setting("default_ai_provider", "Local")
            host.ui.write_log(
                "🔒 SYSTEM: Air-Gapped Offline Mode ENGAGED. All operations running 100% locally."
            )
            host.speak(
                "Offline mode engaged, sir. Running 100 percent locally on your machine.",
                proactive=True,
            )
            return

        if routing.any_phrase(lower, ONLINE_PHRASES):
            config_manager.set_setting("offline_mode_enabled", False)
            config_manager.set_setting("default_ai_provider", "Google Gemini")
            host.ui.write_log("🌐 SYSTEM: Offline Mode DISENGAGED. Cloud connectivity restored.")
            host.speak("Online mode restored, sir. Cloud connectivity is active.", proactive=True)
            return

        lower = text.lower()
        extract_goal = getattr(m, "_extract_skill_creation_goal", lambda _t: None)
        skill_goal = extract_goal(text)
        if skill_goal is not None:
            if not skill_goal:
                prompt = "What should the new skill or feature do?"
                host.ui.write_log(f"Brahma Evo: {prompt}")
                host.speak(prompt)
                return

            try:
                host.ui.begin_task_workspace(
                    text,
                    [
                        "Generate the requested feature",
                        "Validate it in the skill sandbox",
                        "Register it for future use",
                    ],
                    source=source or "local",
                )
            except Exception:
                pass

            host.speak("Starting feature creation now.")
            thread_cls(
                target=host._forge_skill,
                args=(skill_goal,),
                daemon=True,
                name="skill-forge",
            ).start()
            return

        try:
            from core.dynamic_registry import DynamicToolRegistry

            matched = DynamicToolRegistry.find_matching_skill(text)
            if matched:
                skill_name, skill_args = matched
                host.speak(f"Running {skill_name.replace('_', ' ')}.")

                def _run_dynamic_feature():
                    try:
                        res = DynamicToolRegistry.execute_sync(skill_name, skill_args or {})
                        if isinstance(res, dict):
                            out_text = str(
                                res.get("summary") or res.get("output") or res.get("text") or res
                            ).strip()
                        else:
                            out_text = str(res).strip()
                        host.ui.write_log(f"Brahma Evo [{skill_name}]:\n{out_text}")
                        if hasattr(host.ui, "finish_task_workspace"):
                            host.ui.finish_task_workspace(out_text, f"{skill_name} completed.", 100)
                        if hasattr(host.ui, "show_hud_deliverable"):
                            host.ui.show_hud_deliverable(
                                title=f"{skill_name.upper()} DELIVERABLE",
                                summary=out_text,
                                kind=skill_name,
                            )
                        first_line = (
                            out_text.strip().splitlines()[0]
                            if out_text.strip()
                            else "Task completed."
                        )
                        if len(first_line) > 120:
                            first_line = first_line[:115] + "..."
                        host.speak(first_line)
                    except Exception as err:
                        host.ui.write_log(f"ERR: {skill_name} failed: {err}")
                        host.speak(f"Error running {skill_name}: {err}")

                thread_cls(
                    target=_run_dynamic_feature, daemon=True, name=f"skill-{skill_name}"
                ).start()
                return
        except Exception as e_dyn:
            print(f"[Registry] Match error: {e_dyn}")

        if (
            any(routing.normalize(text).startswith(prefix) for prefix in ("email ", "mail "))
            or routing.any_phrase(lower, ("send email", "write email", "compose email"))
        ):
            rem = text
            for prefix in (
                "send email",
                "send an email",
                "write email",
                "write an email",
                "compose email",
                "compose an email",
                "email",
                "mail",
            ):
                if rem.lower().strip().startswith(prefix):
                    rem = rem.strip()[len(prefix) :].strip()
                    break
            if rem.lower().startswith("to "):
                rem = rem[3:].strip()
            recipient = rem.strip()
            host._email_mode = True
            host._email_recipient = recipient
            try:
                host.ui.begin_task_workspace(
                    text,
                    [
                        "Identify the recipient",
                        "Select email application",
                        "Collect message content",
                        "Open application & compose",
                    ],
                    source=source or "local",
                )
            except Exception:
                pass
            if not recipient:
                host._email_step = 0
                prompt = "Who would you like to send the email to?"
                host.ui.write_log(f"Brahma Evo: {prompt}")
                host.speak(prompt)
                try:
                    host.ui.update_task_workspace(
                        status="Identifying recipient",
                        output="Asking for email recipient...",
                        percent=10,
                    )
                except Exception:
                    pass
            else:
                host._email_step = 1
                prompt = "Which email app would you like to use? (Gmail, default mail app, etc.)"
                host.ui.write_log(f"Brahma Evo: {prompt}")
                host.speak(prompt)
                try:
                    host.ui.update_task_workspace(
                        status="Selecting email app",
                        output=f"Recipient identified: {recipient}. Asking for email application...",
                        percent=25,
                    )
                except Exception:
                    pass
            return

        looks_briefing = getattr(m, "_looks_like_daily_briefing_request", lambda _t: False)
        if looks_briefing(text):
            try:
                host.ui.begin_task_workspace(
                    text,
                    [
                        "Collecting weather, schedule, inbox, and headlines",
                        "Delivering your briefing",
                    ],
                    source=source or "local",
                )
            except Exception:
                pass
            host.speak("Preparing your daily briefing.")

            def _run_daily_briefing():
                try:
                    from actions.daily_briefing import daily_briefing

                    daily_briefing(
                        parameters={"category": "all"}, player=host.ui, speak=host.speak
                    )
                except Exception as exc:
                    message = f"Daily briefing failed: {exc}"
                    try:
                        host.ui.write_log(f"ERR: {message}")
                    except Exception:
                        pass
                    host.speak(message)

            thread_cls(
                target=_run_daily_briefing, daemon=True, name="daily-briefing"
            ).start()
            return

        try:
            from smart_home.smart_device_manager import SmartDeviceManager

            sd_mgr = SmartDeviceManager()
            devices = host._smart_home.list_devices()
            routed_text_home = sd_mgr.route_command(text, devices)
            if routed_text_home != text:
                print(f"[BRAHMA EVO] Redirection: '{text}' -> '{routed_text_home}'")
                text = routed_text_home
        except Exception as e:
            print(f"[BRAHMA EVO] Redirection error: {e}")

        developer_settings = (
            host.ui._load_app_settings() if hasattr(host.ui, "_load_app_settings") else {}
        )
        developer_workspace = str(
            developer_settings.get("developer_mode_workspace", "")
        ).strip()
        if not developer_workspace:
            developer_workspace = str(Path.home() / "Desktop" / "BrahmaProjects")
            Path(developer_workspace).mkdir(parents=True, exist_ok=True)

        looks_pres = getattr(m, "_looks_like_presentation_request", lambda _t: False)
        looks_sheet = getattr(m, "_looks_like_spreadsheet_request", lambda _t: False)
        presentation_request = looks_pres(text)
        spreadsheet_request = looks_sheet(text)

        if presentation_request:
            host.speak("Designing your presentation slides...")
            if hasattr(host.ui, "begin_task_workspace"):
                host.ui.begin_task_workspace(
                    text,
                    [
                        "Researching topic",
                        "Outlining slides",
                        "Applying typography & theme",
                        "Generating PowerPoint deck",
                    ],
                    source=source or "local",
                )

            def _run_presentation():
                try:
                    from actions.office_generator import generate_presentation_from_prompt

                    res = generate_presentation_from_prompt(
                        text, player=host.ui, speak=host.speak
                    )
                    host.ui.write_log(f"[BrahmaOffice] {res}")
                    if hasattr(host.ui, "update_task_workspace"):
                        host.ui.update_task_workspace(
                            status="Presentation Completed", output=res, percent=100
                        )
                    host.speak(
                        "Your presentation has been created and saved to Desktop, sir."
                    )
                except Exception as exc:
                    host.ui.write_log(f"ERR: Presentation generation failed: {exc}")
                    if hasattr(host.ui, "update_task_workspace"):
                        host.ui.update_task_workspace(
                            status="Generation Failed", output=str(exc), percent=0
                        )
                    host.speak(
                        "There was an issue creating the presentation, sir. Please check the logs."
                    )

            thread_cls(target=_run_presentation, daemon=True).start()
            return

        if spreadsheet_request:
            host.speak("Building your spreadsheet workbook...")
            if hasattr(host.ui, "begin_task_workspace"):
                host.ui.begin_task_workspace(
                    text,
                    [
                        "Analyzing data structure",
                        "Defining headers & formulas",
                        "Formatting workbook",
                        "Saving Excel spreadsheet",
                    ],
                    source=source or "local",
                )

            def _run_spreadsheet():
                try:
                    from actions.office_generator import generate_spreadsheet_from_prompt

                    res = generate_spreadsheet_from_prompt(
                        text, player=host.ui, speak=host.speak
                    )
                    host.ui.write_log(f"[BrahmaOffice] {res}")
                    if hasattr(host.ui, "update_task_workspace"):
                        host.ui.update_task_workspace(
                            status="Spreadsheet Completed", output=res, percent=100
                        )
                    host.speak(
                        "Your spreadsheet workbook has been created and saved to Desktop, sir."
                    )
                except Exception as exc:
                    host.ui.write_log(f"ERR: Spreadsheet generation failed: {exc}")
                    if hasattr(host.ui, "update_task_workspace"):
                        host.ui.update_task_workspace(
                            status="Generation Failed", output=str(exc), percent=0
                        )
                    host.speak(
                        "There was an issue creating the spreadsheet, sir. Please check the logs."
                    )

            thread_cls(target=_run_spreadsheet, daemon=True).start()
            return

        looks_web = getattr(m, "_looks_like_website_request", lambda _t: False)
        looks_code = getattr(m, "_looks_like_code_request", lambda _t: False)
        website_request = looks_web(text)
        code_request = (
            not presentation_request
            and not spreadsheet_request
            and looks_code(text)
            and looks_like_software_request(text)
        )

        if website_request or code_request:
            host.speak("Working on your project with Brahma Dev...")
            if hasattr(host.ui, "begin_task_workspace"):
                host.ui.begin_task_workspace(
                    text,
                    [
                        "Analyzing specifications",
                        "Scaffolding files",
                        "Writing code",
                        "Verifying build",
                    ],
                    source=source or "local",
                )

            def _run_brahma_dev():
                try:
                    import webbrowser
                    from actions.brahma_dev_agent import run_dev_agent

                    res = run_dev_agent(
                        {"description": text, "workspace_path": developer_workspace},
                        speak=host.speak,
                    )
                    host.ui.write_log(f"[BrahmaDev] {res[:400]}")
                    if "encountered an error during inference" in res or "LLM error:" in res:
                        if hasattr(host.ui, "update_task_workspace"):
                            host.ui.update_task_workspace(
                                status="Build Error", output=res, percent=0
                            )
                        host.speak(
                            "There was an error building the project, sir. Please check the logs."
                        )
                        return
                    if hasattr(host.ui, "update_task_workspace"):
                        host.ui.update_task_workspace(
                            status="Project Completed", output=res, percent=100
                        )
                    folder_display = (
                        Path(developer_workspace).name or developer_workspace
                    )
                    host.speak(
                        f"Your project has been completed and saved to {folder_display}, sir."
                    )
                    try:
                        idx_path = Path(developer_workspace) / "index.html"
                        if idx_path.exists():
                            webbrowser.open(str(idx_path.resolve()))
                    except Exception:
                        pass
                except Exception as exc:
                    host.ui.write_log(f"ERR: Brahma Dev failed: {exc}")
                    if hasattr(host.ui, "update_task_workspace"):
                        host.ui.update_task_workspace(
                            status="Build Failed", output=str(exc), percent=0
                        )
                    host.speak(
                        "There was an issue building the project, sir. Please check the logs."
                    )

            thread_cls(target=_run_brahma_dev, daemon=True).start()
            return

        lower_cmd = text.lower().strip()
        is_email_check = routing.any_phrase(lower_cmd, EMAIL_CHECK_PHRASES)
        is_calendar_check = routing.any_phrase(lower_cmd, CALENDAR_CHECK_PHRASES)

        if is_email_check:
            host.speak("Checking your Gmail inbox, sir...")
            if hasattr(host.ui, "begin_task_workspace"):
                host.ui.begin_task_workspace(
                    text,
                    [
                        "Connecting to Gmail",
                        "Fetching unread messages",
                        "Parsing headers & body",
                        "Summarizing inbox",
                    ],
                    source=source or "local",
                )

            def _run_gw_email():
                try:
                    from actions.google_workspace_mcp import google_workspace

                    res = google_workspace(
                        {"service": "gmail", "action": "unread"},
                        player=host.ui,
                        speak=host.speak,
                    )
                    host.ui.write_log(f"[GoogleWorkspace] {res}")
                    if hasattr(host.ui, "update_task_workspace"):
                        host.ui.update_task_workspace(
                            status="Email Check Completed", output=res, percent=100
                        )
                    host.speak("Here are your latest emails, sir.")
                except Exception as exc:
                    host.ui.write_log(f"ERR: Gmail check failed: {exc}")
                    if hasattr(host.ui, "update_task_workspace"):
                        host.ui.update_task_workspace(
                            status="Email Check Failed", output=str(exc), percent=0
                        )
                    host.speak(
                        "Unable to check emails. Please ensure your credentials are set up in Settings."
                    )

            thread_cls(target=_run_gw_email, daemon=True).start()
            return

        if is_calendar_check:
            host.speak("Checking your calendar schedule...")
            if hasattr(host.ui, "begin_task_workspace"):
                host.ui.begin_task_workspace(
                    text,
                    [
                        "Connecting to Calendar",
                        "Retrieving scheduled events",
                        "Formatting timeline",
                    ],
                    source=source or "local",
                )

            def _run_gw_calendar():
                try:
                    from actions.google_workspace_mcp import google_workspace

                    res = google_workspace(
                        {"service": "calendar", "action": "list"},
                        player=host.ui,
                        speak=host.speak,
                    )
                    host.ui.write_log(f"[GoogleWorkspace] {res}")
                    if hasattr(host.ui, "update_task_workspace"):
                        host.ui.update_task_workspace(
                            status="Schedule Retrieved", output=res, percent=100
                        )
                    host.speak("Here is your schedule, sir.")
                except Exception as exc:
                    host.ui.write_log(f"ERR: Calendar check failed: {exc}")
                    if hasattr(host.ui, "update_task_workspace"):
                        host.ui.update_task_workspace(
                            status="Schedule Check Failed", output=str(exc), percent=0
                        )
                    host.speak("There was an issue checking your schedule, sir.")

            thread_cls(target=_run_gw_calendar, daemon=True).start()
            return

        is_ram_check = routing.any_phrase(lower_cmd, RAM_CHECK_PHRASES)
        is_battery_check = routing.any_phrase(lower_cmd, BATTERY_CHECK_PHRASES)
        is_brightness_req = routing.any_phrase(lower_cmd, BRIGHTNESS_PHRASES)
        is_kill_req = (
            lower_cmd.startswith("kill ")
            or lower_cmd.startswith("terminate ")
            or lower_cmd.startswith("force kill ")
        ) and len(lower_cmd.split()) <= 4
        is_diag_req = routing.any_phrase(lower_cmd, DIAGNOSTIC_PHRASES)

        if is_ram_check:
            host.speak("Scanning memory consumers...")

            def _run_ram():
                from actions.system_diagnostics_mcp import system_diagnostics

                res = system_diagnostics(
                    {"action": "ram_hogs"}, player=host.ui, speak=host.speak
                )
                host.ui.write_log(f"[Diagnostics] {res}")
                if hasattr(host.ui, "finish_task_workspace"):
                    host.ui.finish_task_workspace(res, "Memory analysis complete.", 100)

            thread_cls(target=_run_ram, daemon=True).start()
            return

        if is_battery_check:
            def _run_battery():
                from actions.system_diagnostics_mcp import system_diagnostics

                res = system_diagnostics(
                    {"action": "battery"}, player=host.ui, speak=host.speak
                )
                host.ui.write_log(f"[Diagnostics] {res}")
                if hasattr(host.ui, "finish_task_workspace"):
                    host.ui.finish_task_workspace(res, "Battery check complete.", 100)

            thread_cls(target=_run_battery, daemon=True).start()
            return

        if is_brightness_req:
            import re

            match = re.search(r"(\d+)", lower_cmd)
            lvl = int(match.group(1)) if match else None
            rel = (
                "dim" in lower_cmd
                or "lower" in lower_cmd
                or "increase" in lower_cmd
                or "boost" in lower_cmd
            )
            if rel and lvl is None:
                lvl = -20 if ("dim" in lower_cmd or "lower" in lower_cmd) else 20

            def _run_brightness():
                from actions.system_diagnostics_mcp import system_diagnostics

                p: dict[str, Any] = {"action": "brightness"}
                if lvl is not None:
                    p["level"] = lvl
                    p["relative"] = rel
                res = system_diagnostics(p, player=host.ui, speak=host.speak)
                host.ui.write_log(f"[Diagnostics] {res}")
                if hasattr(host.ui, "finish_task_workspace"):
                    host.ui.finish_task_workspace(res, "Brightness adjusted.", 100)

            thread_cls(target=_run_brightness, daemon=True).start()
            return

        if is_kill_req:
            target_app = lower_cmd.split(None, 1)[1].strip()

            def _run_kill():
                from actions.system_diagnostics_mcp import system_diagnostics

                res = system_diagnostics(
                    {
                        "action": "kill",
                        "target": target_app,
                        "force": "force" in lower_cmd,
                    },
                    player=host.ui,
                    speak=host.speak,
                )
                host.ui.write_log(f"[Diagnostics] {res}")
                if hasattr(host.ui, "finish_task_workspace"):
                    host.ui.finish_task_workspace(
                        res, "Process management complete.", 100
                    )

            thread_cls(target=_run_kill, daemon=True).start()
            return

        if is_diag_req:
            host.speak("Retrieving system diagnostics report...")

            def _run_diag():
                from actions.system_diagnostics_mcp import system_diagnostics

                res = system_diagnostics(
                    {"action": "status"}, player=host.ui, speak=host.speak
                )
                host.ui.write_log(f"[Diagnostics] {res}")
                if hasattr(host.ui, "finish_task_workspace"):
                    host.ui.finish_task_workspace(res, "System report ready.", 100)

            thread_cls(target=_run_diag, daemon=True).start()
            return

        is_trigger_bug = routing.any_phrase(lower_cmd, BUG_TRIGGER_PHRASES)
        is_heal_cmd = routing.any_phrase(lower_cmd, HEAL_PHRASES)
        is_rollback_cmd = routing.any_phrase(lower_cmd, ROLLBACK_PHRASES)
        is_patch_history = routing.any_phrase(lower_cmd, PATCH_HISTORY_PHRASES)
        is_learn_rule = (
            lower_cmd.startswith("remember to ")
            or lower_cmd.startswith("remember that ")
            or (
                lower_cmd.startswith("always ")
                and len(lower_cmd.split()) > 2
                and not lower_cmd.startswith("always open")
            )
            or (lower_cmd.startswith("never ") and len(lower_cmd.split()) > 2)
            or "new rule:" in lower_cmd
        )

        if is_trigger_bug:
            def _run_test_bug():
                try:
                    from actions.test_action import test_action

                    res = test_action({}, player=host.ui, speak=host.speak)
                    host.ui.write_log(f"[TestAction] {res}")
                    host.speak(
                        f"Test action succeeded with no errors: {res}. The bug appears already healed!"
                    )
                except Exception as exc:
                    import traceback
                    from actions.auto_heal_engine import AutoHealEngine

                    tb_str = traceback.format_exc()
                    AutoHealEngine.record_last_error(tb_str)
                    err_msg = (
                        f"Simulated bug triggered in test_action.py: {type(exc).__name__}. "
                        "Traceback captured! You can now say 'Brahma, fix that bug'."
                    )
                    host.speak(err_msg)
                    host.ui.write_log(f"[AutoHeal Test] {err_msg}")
                    if hasattr(host.ui, "finish_task_workspace"):
                        host.ui.finish_task_workspace(
                            tb_str, "Simulated bug captured.", 100
                        )

            thread_cls(target=_run_test_bug, daemon=True).start()
            return

        if is_heal_cmd:
            host.speak("Analyzing last captured traceback and synthesizing hotfix...")

            def _run_heal():
                from actions.auto_heal_engine import auto_heal

                res = auto_heal({"action": "heal"}, player=host.ui, speak=host.speak)
                host.ui.write_log(f"[AutoHeal] {res}")
                if hasattr(host.ui, "finish_task_workspace"):
                    host.ui.finish_task_workspace(res, "Self-patching complete.", 100)

            thread_cls(target=_run_heal, daemon=True).start()
            return

        if is_rollback_cmd:
            def _run_rollback():
                from actions.auto_heal_engine import auto_heal

                res = auto_heal(
                    {"action": "rollback"}, player=host.ui, speak=host.speak
                )
                host.ui.write_log(f"[AutoHeal] {res}")
                if hasattr(host.ui, "finish_task_workspace"):
                    host.ui.finish_task_workspace(res, "Rollback complete.", 100)

            thread_cls(target=_run_rollback, daemon=True).start()
            return

        if is_patch_history:
            def _run_history():
                from actions.auto_heal_engine import auto_heal

                action_type = "status" if "status" in lower_cmd else "history"
                res = auto_heal(
                    {"action": action_type}, player=host.ui, speak=host.speak
                )
                host.ui.write_log(f"[AutoHeal] {res}")
                if hasattr(host.ui, "finish_task_workspace"):
                    host.ui.finish_task_workspace(res, "Auto-heal report ready.", 100)

            thread_cls(target=_run_history, daemon=True).start()
            return

        if is_learn_rule:
            rule_raw = (
                lower_cmd.replace("remember that ", "")
                .replace("remember to ", "")
                .replace("new rule: ", "")
                .strip()
            )

            def _run_learn():
                from core.learned_rules import LearnedRulesEngine

                res = LearnedRulesEngine.add_rule(rule_raw, origin="voice_directive")
                msg = res.get(
                    "message",
                    f"Understood. I have committed '{rule_raw}' to my continuous memory.",
                )
                host.speak(msg)
                host.ui.write_log(f"[LearnedRules] {msg}")
                if hasattr(host.ui, "finish_task_workspace"):
                    host.ui.finish_task_workspace(msg, "Rule learned.", 100)

            thread_cls(target=_run_learn, daemon=True).start()
            return

        is_volume_cmd = routing.any_phrase(lower_cmd, VOLUME_PHRASES) or (
            lower_cmd in ("mute", "unmute", "volume max")
        )
        if is_volume_cmd and not routing.any_phrase(
            lower_cmd, ("phone", "mobile", "android", "tablet")
        ):
            def _run_volume():
                import re
                from actions.computer_settings import (
                    volume_down,
                    volume_mute,
                    volume_set,
                    volume_up,
                )

                if any(w in lower_cmd for w in ("up", "increase", "higher", "raise")):
                    volume_up()
                    host.speak("Volume increased.")
                elif any(w in lower_cmd for w in ("down", "decrease", "lower")):
                    volume_down()
                    host.speak("Volume decreased.")
                elif "unmute" in lower_cmd or "mute" in lower_cmd:
                    volume_mute()
                    host.speak("Audio toggled.")
                else:
                    match = re.search(r"(\d+)", lower_cmd)
                    if match:
                        val = int(match.group(1))
                        volume_set(val)
                        host.speak(f"Volume set to {val} percent.")
                    else:
                        volume_up()
                        host.speak("Volume adjusted.")

            thread_cls(target=_run_volume, daemon=True).start()
            return

        has_mobile_target = routing.any_phrase(lower_cmd, MOBILE_TARGET_PHRASES)
        is_open_app_cmd = (
            any(lower_cmd.startswith(prefix) for prefix in OPEN_APP_PREFIXES)
            and not has_mobile_target
            and not routing.any_phrase(
                lower_cmd,
                (
                    "website",
                    "url",
                    "http",
                    "presentation",
                    "sheet",
                    "spreadsheet",
                    "project",
                    "code",
                    "game",
                ),
            )
        )
        if is_open_app_cmd:
            from actions.open_app import _APP_ALIASES, open_app

            clean_app_candidate = lower_cmd
            for prefix in OPEN_APP_PREFIXES:
                if clean_app_candidate.startswith(prefix):
                    clean_app_candidate = clean_app_candidate[len(prefix) :].strip()
                    break
            # Exact alias only: a near miss ("chrome browser") goes to the
            # model, which has the same open_app tool. The old substring test
            # also matched an empty candidate, so a bare "open" claimed a turn.
            app_alias = routing.exact_alias(clean_app_candidate, _APP_ALIASES)
            if app_alias:
                clean_app_candidate = app_alias

                def _run_open_local_app():
                    open_app({"app_name": clean_app_candidate}, player=host.ui)
                    host.speak(f"Opening {clean_app_candidate}, sir.")

                thread_cls(target=_run_open_local_app, daemon=True).start()
                return

        mem_ctx_fn = getattr(m, "_memory_context_for_request", lambda _t: "")
        memory_ctx = mem_ctx_fn(text)
        routed_text = (
            f"{memory_ctx}\n\nCurrent User Request:\n{text}" if memory_ctx else text
        )
        if source == "instagram":
            routed_text = (
                f"Owner sent this via Instagram DM: {text}\n"
                "(SYSTEM: If this is an action like opening an app or running a command, "
                "you MUST execute it using your tools rather than just replying with text.)"
            )
        if any(
            routing.is_phrase(text, phrase)
            for phrase in ("stop meeting mode", "end meeting mode", "close meeting mode")
        ):
            host._stop_meeting_mode("Meeting mode closed.")
            return
        if host._handle_attention_response(text):
            return
        try:
            plan_fn = getattr(m, "_build_task_plan", lambda _t: [])
            host.ui.begin_task_workspace(text, plan_fn(text), source=source or "local")
        except Exception:
            pass
        if source != "instagram" and host._handle_brahma_connect_command(
            text, source=source or "local"
        ):
            return
        # Smart-home is a model tool choice, not a pre-LLM utterance route.
        # See docs/ROUTING-AUDIT.md — a pre-LLM device matcher claimed semantic
        # authority it cannot have and could not fall through on a false match.
        looks_screen = getattr(m, "_looks_like_screen_request", lambda _t: False)
        if looks_screen(text):
            try:
                host.ui.update_task_workspace(
                    status="Scanning screen",
                    output="Brahma Evo is inspecting the screen for what you asked about.",
                    percent=40,
                )
            except Exception:
                pass
            img_bytes = (
                host.ui.capture_screen_bytes()
                if hasattr(host.ui, "capture_screen_bytes")
                else None
            )

            def _run_screen_process():
                screen_fn = getattr(m, "screen_process", None)
                if screen_fn is None:
                    from actions.screen_processor import screen_process as screen_fn
                success = screen_fn(
                    parameters={"angle": "screen", "text": text},
                    response=None,
                    player=host.ui,
                    session_memory=None,
                    image_bytes=img_bytes,
                )
                if not success:
                    try:
                        host.ui.update_task_workspace(
                            status="Screen analysis failed",
                            output=(
                                "Screen analysis could not complete. "
                                "Check your internet connection, API key, or screen capture permissions."
                            ),
                            percent=0,
                        )
                    except Exception:
                        pass

            thread_cls(target=_run_screen_process, daemon=True).start()
            return

        app_settings = config_manager.load_settings()
        configured_provider = app_settings.get("default_ai_provider", "Gemini")
        is_offline_mode = bool(app_settings.get("offline_mode_enabled", False))
        is_local_preferred = is_offline_mode or configured_provider == "Local"

        if (
            is_local_preferred
            or host._prefer_openrouter()
            or not host._loop
            or not host.session
            or host._voice_backend() != "gemini_live"
        ):
            thread_cls(
                target=host._fallback_reply, args=(text, memory_ctx), daemon=True
            ).start()
            return
        host.ui.set_state("THINKING")
        asyncio.run_coroutine_threadsafe(
            host.session.send_client_content(
                turns={"parts": [{"text": routed_text}]},
                turn_complete=True,
            ),
            host._loop,
        )

    def deliver_avatar_emotion(self, emotion: str) -> None:
        """Hand one already-stripped emotion tag to the UI and the avatar seam."""
        if not emotion:
            return
        print(f"[BRAHMA EVO] 🎭 Emotion tag: {emotion}")
        try:
            if hasattr(self._host.ui, "set_emotion_tag"):
                self._host.ui.set_emotion_tag(emotion)
        except Exception:
            pass
        try:
            from core import avatar_runtime

            record = avatar_runtime.handle_emotion_tag(emotion)
            if record.get("delivered"):
                print(
                    f"[BRAHMA EVO] 🎭 Avatar: {record['tag']} -> {record['backend']}"
                )
        except Exception:
            pass

    def ensure_agent_session(self, request_text: str, model: str = ""):
        """Return the durable GUI session, creating it on the first turn."""
        host = self._host
        if getattr(host, "_agent_session", None) is not None:
            return host._agent_session
        from agent import session_bridge as bridge
        from agent.session import AgentSession

        workspace = ""
        try:
            from agent.runtime import current as current_agent_runtime

            workspace = str(current_agent_runtime().workspace)
        except Exception:
            workspace = ""
        workspace = workspace or os.getcwd()
        try:
            host._agent_session = AgentSession.create(
                workspace,
                title=bridge.title_for(request_text),
                model=model or "",
            )
        except Exception as exc:
            print(f"[BRAHMA EVO] \u26a0\ufe0f session persistence unavailable: {exc}")
            host._agent_session = None
        return host._agent_session

    def record_voice_turn(self, user_text: str, assistant_text: str) -> None:
        """Persist one completed Gemini Live turn into the current session."""
        if not (user_text or assistant_text):
            return
        from agent import session_bridge as bridge

        session = self.ensure_agent_session(
            user_text or "Voice session", bridge.VOICE_MODEL
        )
        if session is None:
            return
        written = bridge.record_voice_turn(session, user_text, assistant_text)
        if written:
            print(
                f"[BRAHMA EVO] \U0001f5c2\ufe0f voice turn persisted ({written} message(s))"
            )

    def with_agent_mcp(self, tools: list) -> list:
        """MCP tools merged into a model-facing tool list, started on first use."""
        m = _main()
        try:
            from agent.mcp import with_mcp_tools
            from agent.runtime import current as current_agent_runtime
        except Exception as exc:
            print(f"[BRAHMA EVO] \u26a0\ufe0f MCP unavailable: {exc}")
            return list(tools or [])

        manager = m._harness_mcp_manager()
        try:
            runtime = current_agent_runtime()
            workspace = runtime.workspace
            approver = getattr(runtime, "approver", None)
            headless = bool(getattr(runtime, "headless", False))
            trust_prompt = None
            if callable(approver) and not headless:
                def _ask_trust(ws, configs):
                    names = [cfg.name for cfg in configs]
                    count = len(names)
                    noun = "server" if count == 1 else "servers"
                    detail = (
                        f"This workspace ({ws}) wants to start {count} MCP {noun}: "
                        f"{', '.join(names)}."
                    )
                    ans = approver(
                        "mcp_workspace_trust",
                        {"workspace": str(ws), "servers": names},
                        detail,
                    )
                    return bool(ans)

                trust_prompt = _ask_trust
            if not manager.ensure(
                workspace,
                on_event=self._host._agent_event,
                trust_prompt=trust_prompt,
            ):
                return list(tools or [])
            return with_mcp_tools(tools)
        except Exception as exc:
            print(f"[BRAHMA EVO] \u26a0\ufe0f MCP unavailable: {exc}")
            return list(tools or [])

    def local_brain_reply(self, request_text: str, local_model_target: str) -> str:
        """One full Local Brain turn: bounded tools, execution, agentic loop."""
        host = self._host
        m = _main()
        host._ensure_agent_session(request_text, local_model_target)
        messages = host._agentic_seed_messages(request_text)
        local_tools, _skill_ctx = local_brain.resolve_tools(
            request_text, m.TOOL_DECLARATIONS
        )
        local_tools = host._with_agent_mcp(local_tools)
        print(
            f"[BRAHMA EVO] \U0001f9f0 Local turn: prompt tier "
            f"'{m._prompt_tier()}', {len(messages[0]['content'])} chars, "
            f"{len(local_tools)} tool(s) for {local_model_target}."
        )
        res = local_brain.chat_complete(
            messages, model=local_model_target, tools=local_tools, focus_core=True
        )
        return host._finish_tool_turn(
            res,
            messages,
            source="local",
            model_label=local_model_target,
            tools=local_tools,
        )

    def agentic_seed_messages(
        self, request_text: str, *, advertise_discovery: bool = True
    ) -> list:
        """The first messages of an agentic turn: system prompt + user request."""
        m = _main()
        core_prompt, manual = m._prompt_core_and_manual()
        if not core_prompt:
            core_prompt = "You are Brahma Evo, the autonomous desktop operating system."
        tier = m._prompt_tier()
        system_prompt = core_prompt
        if manual and tier == "full":
            system_prompt = f"{system_prompt}\n\n{manual}"

        system_prompt = (
            f"{system_prompt}\n\n"
            "CRITICAL OPERATING SYSTEM DIRECTIVE:\n"
            "- You have FULL DIRECT ACCESS and authority over this Windows PC via your tools.\n"
            "- NEVER state that you are a text-based AI, that you cannot perform automations, or that you lack real-time access.\n"
            "- Whenever the user requests an action (opening an app, changing volume, setting a reminder, running a protocol, organizing files, searching, etc.), ALWAYS call the corresponding tool.\n"
            "- Keep responses concise, direct, and conversational."
        )

        if manual and tier != "full" and advertise_discovery:
            system_prompt = (
                f"{system_prompt}\n\n"
                "The full per-domain tool manual is available on demand: get it "
                "with the get_tool_surface tool (call it with no arguments) "
                "whenever the right tool for a request is not obvious. "
                "search_tools finds a tool by goal; call_tool runs the exact "
                "name it returns. Never claim a capability is missing before "
                "you have looked."
            )

        if advertise_discovery:
            _tools, skill_ctx = local_brain.resolve_tools(
                request_text, m.TOOL_DECLARATIONS
            )
            if skill_ctx:
                system_prompt = f"{system_prompt}\n\n{skill_ctx}"

        try:
            from agent.prompts import brain_prompt_block, project_context
            from agent.runtime import current as current_agent_runtime

            project = project_context(current_agent_runtime().workspace)
        except Exception:
            project = ""
        if project:
            system_prompt = f"{system_prompt}\n\n{project}"

        # The resident map of the user's second brain. The knowledge itself is
        # injected per turn by AgentLoop (``second_brain.context_for_turn``);
        # this block only tells the model that the vault exists and how to ask
        # for more depth.
        try:
            brain_map = brain_prompt_block()
        except Exception:
            brain_map = ""
        if brain_map:
            system_prompt = f"{system_prompt}\n\n{brain_map}"

        # The specialist-template catalogue is machine configuration rather
        # than workspace context, so ``project_context`` does not carry it. It
        # is a compact index (categories + examples), not the 600 bodies; the
        # model calls list_subagents for detail and run_subagent to dispatch.
        try:
            from agent.subagent_library import prompt_block as subagent_prompt_block

            subagent_index = subagent_prompt_block()
        except Exception:
            subagent_index = ""
        if subagent_index:
            system_prompt = f"{system_prompt}\n\n{subagent_index}"

        try:
            from core import instruction_injector

            layers = ["custom", "local"] if advertise_discovery else ["local"]
            for layer in layers:
                try:
                    injected = instruction_injector.compose(layer)
                except Exception:
                    injected = ""
                if injected:
                    system_prompt = f"{system_prompt}{injected}"
        except Exception:
            pass

        from agent import session_bridge as bridge

        history = bridge.history(getattr(self._host, "_agent_session", None))
        return [
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": request_text},
        ]

    def finish_tool_turn(
        self,
        res: Any,
        messages: list,
        *,
        source: str,
        model_label: str,
        tools: list | None = None,
    ) -> str:
        """Continue an OpenAI-protocol turn until the model stops calling tools."""
        host = self._host
        m = _main()
        from agent import session_bridge as bridge
        from agent.loop import AgentLoop
        from agent.providers import custom_stream
        from agent.runtime import current as current_agent_runtime
        from agent.spec import all_specs
        from agent.streaming import collect as collect_stream

        surface: list = []
        try:
            surface = local_brain.format_tools_for_local(list(tools or []))
        except Exception:
            surface = []
        known = {
            entry["function"]["name"]
            for entry in surface
            if isinstance(entry, dict) and isinstance(entry.get("function"), dict)
        }
        for spec in all_specs():
            if desktop_tools.DESKTOP_TAG not in spec.tags and spec.name not in known:
                surface.append(spec.openai())

        if source == "local":
            def _complete(msgs, tool_list):
                return local_brain.chat_complete(
                    msgs, model=model_label, tools=tool_list, focus_core=True
                )

            def _stream(msgs, tool_list, on_delta):
                return collect_stream(
                    local_brain.generate_chat_stream(
                        msgs, model=model_label, tools=tool_list
                    ),
                    on_delta,
                )

            _complete.stream = _stream
        else:
            def _complete(msgs, tool_list):
                return openrouter_client.chat_as_completion(
                    "custom", "", full_messages=msgs, tools=tool_list
                )

            def _stream(msgs, tool_list, on_delta):
                return custom_stream(
                    msgs, tool_list, on_delta, temperature=0.7, max_tokens=4096
                )

            _complete.stream = _stream

        seeded = list(messages or [])
        context = bridge.seed_context(seeded)

        session = getattr(host, "_agent_session", None)
        if session is not None:
            try:
                pending = bridge.messages_after(
                    bridge.history(session), context.messages
                )
                for message in pending:
                    session.append_message(message)
            except Exception as exc:
                print(f"[BRAHMA EVO] \u26a0\ufe0f session write failed: {exc}")

        runtime = current_agent_runtime()
        desktop_tools.attach_host(host)
        runtime.reset_cancel()
        loop = AgentLoop(
            _complete,
            tools=surface,
            native_dispatch=host._execute_tool_sync,
            runtime=runtime,
            context=context,
            session=session,
            max_turns=getattr(m, "AGENT_MAX_TURNS", 24),
            on_event=host._agent_event,
            cancel=runtime.cancel_event,
            stream=source in ("local", "custom"),
        )
        try:
            result = loop.run(first_response=res)
        except Exception as exc:
            print(f"[BRAHMA EVO] \u26a0\ufe0f Agent loop failed: {exc}")
            return f"The agent loop stopped unexpectedly: {exc}"

        if result.text:
            return result.text
        if result.stop_reason != "completed":
            return (
                f"I stopped after {result.turns} step(s) "
                f"({result.stop_reason.replace('_', ' ')})."
            )
        return f"{model_label} returned no usable response."

    def agent_event(self, event: dict) -> None:
        """Mirror loop telemetry into the console and the task workspace."""
        host = self._host
        try:
            kind = event.get("type")
            if getattr(host, "_agent_stream_open", False) and kind != "delta":
                print()
                host._agent_stream_open = False
            if kind == "delta":
                channel = event.get("kind")
                if channel == "text":
                    text = event.get("text") or ""
                    if text:
                        print(text, end="", flush=True)
                        host._agent_stream_open = True
                elif channel == "reset":
                    print("\n[BRAHMA EVO] \u21ba partial response discarded, retrying")
                return
            if kind == "tool_call":
                print(
                    f"[BRAHMA EVO] \U0001f6e0 {event.get('name')} {event.get('arguments')}"
                )
            elif kind == "compaction":
                print(
                    f"[BRAHMA EVO] \U0001f5dc context compacted "
                    f"({event.get('dropped')} message(s))"
                )
            elif kind == "permission":
                print(
                    f"[BRAHMA EVO] \U0001f510 {event.get('decision')}: "
                    f"{event.get('name')} ({event.get('rule')})"
                )
            elif kind == "retry":
                print(
                    f"[BRAHMA EVO] \u21bb provider retry {event.get('attempt')} "
                    f"in {event.get('delay_s')}s: {event.get('error')}"
                )
            elif kind == "instructions":
                print(
                    f"[BRAHMA EVO] \U0001f4c4 loaded instructions: "
                    f"{', '.join(event.get('loaded') or [])}"
                )
            elif kind == "policy_reload":
                state = (
                    "reloaded"
                    if event.get("ok")
                    else "reload failed (old rules kept)"
                )
                print(f"[BRAHMA EVO] \U0001f4dc policy {state}")
            elif kind == "mcp":
                if event.get("ok"):
                    print(
                        f"[BRAHMA EVO] \U0001f50c mcp {event.get('name')}: "
                        f"{event.get('tools')} tool(s)"
                    )
                else:
                    print(
                        f"[BRAHMA EVO] \u26a0\ufe0f mcp {event.get('name')}: "
                        f"{event.get('error')}"
                    )
            elif kind == "mcp_refresh":
                state = "restarted" if event.get("restarted") else "re-listed"
                print(
                    f"[BRAHMA EVO] \U0001f504 mcp {event.get('server')} {state}: "
                    f"{event.get('tools')} tool(s)"
                )
            elif kind == "mcp_untrusted":
                print(
                    f"[BRAHMA EVO] \U0001f512 {event.get('message')}"
                )
            elif kind == "error":
                print(f"[BRAHMA EVO] \u26a0\ufe0f {event.get('error')}")
            elif kind == "stream_fallback":
                print(f"[BRAHMA EVO] streaming unavailable: {event.get('reason')}")
            elif kind == "subagent_start":
                print(
                    f"[BRAHMA EVO] \U0001f9ed subagent {event.get('subagent')} "
                    f"started ({', '.join(event.get('tools') or [])}): "
                    f"{event.get('task')}"
                )
            elif kind == "subagent_end":
                print(
                    f"[BRAHMA EVO] \U0001f9ed subagent {event.get('subagent')} "
                    f"{event.get('stop_reason')} after "
                    f"{event.get('turns')} turn(s)"
                )
            elif kind == "subagent_event":
                inner = event.get("event") or {}
                if inner.get("type") == "tool_call":
                    print(
                        f"[BRAHMA EVO] \U0001f9ed {event.get('subagent')} "
                        f"tool {inner.get('name')} {inner.get('arguments')}"
                    )
            elif kind == "stop":
                print(
                    f"[BRAHMA EVO] \U0001f3c1 agent stopped: {event.get('reason')} "
                    f"after {event.get('turns')} turn(s)"
                )
            if kind in ("tool_start", "stop"):
                status = (
                    event.get("name")
                    if kind == "tool_start"
                    else f"Agent {event.get('reason')}"
                )
                host.ui.update_task_workspace(
                    status=str(status or "Agent"), percent=90
                )
        except Exception:
            pass

    def custom_brain_turn(
        self, request_text: str, label: str = "Custom provider"
    ) -> str:
        """One agentic turn against the user's custom OpenAI-compatible endpoint."""
        host = self._host
        m = _main()
        host._ensure_agent_session(request_text, label)
        messages = host._agentic_seed_messages(
            request_text, advertise_discovery=not host._custom_tools_broken
        )
        tools: list = []
        if not host._custom_tools_broken:
            tools, _skill_ctx = local_brain.resolve_tools(
                request_text, m.TOOL_DECLARATIONS
            )
            tools = host._with_agent_mcp(tools)
            tools = local_brain.format_tools_for_local(tools)
        try:
            msg = openrouter_client.chat_as_completion(
                "custom", "", full_messages=messages, tools=tools
            )
        except Exception as exc:
            if not tools or "tool" not in str(exc).lower():
                raise
            host._custom_tools_broken = True
            print(
                f"[BRAHMA EVO] \u26a0\ufe0f {label} rejected the tool surface; "
                "this endpoint will be treated as tool-less for the session."
            )
            msg = openrouter_client.chat_as_completion(
                "custom", "", full_messages=messages
            )
        return host._finish_tool_turn(
            {"choices": [{"message": msg}]},
            messages,
            source="custom",
            model_label=label,
            tools=tools,
        )

    def fallback_reply(self, text: str, memory_ctx: str = "") -> None:
        host = self._host
        m = _main()
        try:
            host.ui.set_state("THINKING")
            try:
                host.ui.update_task_workspace(
                    status="Thinking",
                    output="Brahma Evo is drafting a direct reply.",
                    percent=35,
                )
            except Exception:
                pass
            reply = ""
            request_text = (
                f"{memory_ctx}\n\nCurrent User Request:\n{text}"
                if memory_ctx
                else text
            )

            app_settings = config_manager.load_settings()
            configured_provider = app_settings.get("default_ai_provider", "Gemini")
            local_model_target = app_settings.get("local_ai_model", "qwen2.5:3b")
            is_offline_mode = app_settings.get("offline_mode_enabled", False)
            selected = m._provider_id_for_name(configured_provider)

            active_chain = ["gemini", "openrouter", "local", "custom"]
            try:
                from core import provider_router

                active_chain = provider_router.chain()
                disabled = [
                    p["id"]
                    for p in provider_router.all_providers()
                    if not p["enabled"]
                ]
                if disabled:
                    print(
                        f"[BRAHMA EVO] Provider chain: {provider_router.describe()} "
                        f"(disabled: {', '.join(disabled)})"
                    )
            except Exception:
                pass

            eligible = [
                p
                for p in active_chain
                if not (is_offline_mode and p in ("gemini", "openrouter"))
            ]
            auto_switch = bool(app_settings.get("auto_provider_switch", True))
            order: list = []
            if not auto_switch and selected is not None:
                if selected in eligible:
                    order.append(selected)
            else:
                if (
                    selected == "gemini"
                    and host._prefer_openrouter()
                    and "openrouter" in eligible
                ):
                    order.append("openrouter")
                if selected is not None and selected in eligible:
                    order.append(selected)
                order.extend(p for p in eligible if p not in order)

            attempt_errors: list = []
            host._custom_tools_broken = False

            plain_system = (
                "You are Brahma Evo, a concise, helpful desktop assistant. "
                "Reply naturally and briefly. Do not mention internal implementation details."
                + m._injected_instructions("fallback")
            )
            labels = getattr(m, "PROVIDER_LABELS", {})
            for pid in order:
                label = labels.get(pid, pid)
                try:
                    host.ui.update_task_workspace(
                        status=f"Thinking ({label})",
                        output=f"Processing on {label}...",
                        percent=50,
                    )
                    if pid == "gemini":
                        reply = m._gemini_text_reply(request_text)
                    elif pid == "openrouter":
                        reply = openrouter_client.chat_as(
                            "openrouter", request_text, system=plain_system
                        )
                    elif pid == "custom":
                        reply = host._custom_brain_turn(request_text, label)
                    elif pid == "local":
                        reply = host._local_brain_reply(
                            request_text, local_model_target
                        )
                    else:
                        continue
                except Exception as exc:
                    reason = str(exc).strip() or exc.__class__.__name__
                    attempt_errors.append((label, reason))
                    print(f"[BRAHMA EVO] \u26a0\ufe0f {label} failed: {reason}")
                    if pid == "gemini" and m._is_gemini_limit_error(exc):
                        host._throttled_to_openrouter()
                    continue

                reply = (reply or "").strip()
                if reply:
                    print(f"[BRAHMA EVO] \u2705 {label} answered successfully!")
                    break
                attempt_errors.append((label, "returned an empty response"))

            reply = (reply or "").strip()
            reply, emotion = host._split_emotion_tag(reply)
            host._deliver_avatar_emotion(emotion)
            provider_error = None
            if not reply:
                detail = "; ".join(
                    f"{label}: {reason}" for label, reason in attempt_errors
                )
                if not detail:
                    detail = (
                        "no providers are enabled in Settings > AI Providers"
                        if not order
                        else "no provider was attempted"
                    )
                err = f"No reply from any provider -> {detail}"
                print(f"[BRAHMA EVO] \u26a0\ufe0f {err}")
                host.ui.write_log(f"ERR: {err}")
                provider_error = detail
                reply = (
                    "No AI providers are enabled. Please enable one in Settings."
                    if not order
                    else "I could not reach any configured AI provider. Please check the provider settings."
                )
            host.ui.write_log(f"Brahma Evo: {reply}")
            if not getattr(host.ui, "muted", False):
                host.speak(reply)
            try:
                if provider_error is None:
                    host.ui.finish_task_workspace(reply, "Reply delivered.", 100)
                else:
                    host.ui.finish_task_workspace(
                        provider_error, "No provider replied.", 100
                    )
            except Exception:
                pass

            if not host.ui.muted:
                host.ui.set_state("LISTENING")
        except Exception as e:
            msg = f"Fallback reply failed: {e}"
            print(f"[BRAHMA EVO] ⚠️ {msg}")
            host.ui.write_log(f"ERR: {msg}")
            try:
                host.ui.finish_task_workspace(msg, "Reply failed.", 100)
            except Exception:
                pass
            if not host.ui.muted:
                host.ui.set_state("LISTENING")


__all__ = ["TextTurnController"]
