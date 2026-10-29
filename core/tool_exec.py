"""Tool-call execution, lifted out of main.py's BrahmaLive (P4-19 seam).

Behavior-preserving move: BrahmaLive keeps thin delegating wrappers and hands
this executor a host reference plus the two main.py symbols it needs
(`_ig_gemini_reply`, TOOL_DECLARATIONS), so there is no circular import.
"""
from __future__ import annotations

import asyncio
import json
import threading
import traceback
from pathlib import Path

from google.genai import types

from actions.browser_control   import browser_control
from actions.computer_control  import computer_control
from actions.computer_settings import computer_settings
from actions.brahma_connect    import (
    connect_list_devices,
    connect_get_device,
    connect_get_capabilities,
    connect_execute,
    connect_pair_device,
    connect_disconnect_device,
)
from actions.pdf_tools         import create_pdf
from actions.office_builder     import create_presentation, create_spreadsheet
from actions.desktop           import desktop_control
from actions.file_controller   import file_controller
from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.game_updater      import game_updater
from core.local_brain import local_brain
from actions.open_app          import open_app
from core.protocols import protocols
from actions.reminder          import reminder
from actions.screen_processor  import screen_process
from memory.memory_manager import search_memory
from actions.send_message      import send_message
from core.sensorium import sensorium
from core import undo as undo_stack
from memory.memory_manager import (
    update_memory
)
from actions.weather_report    import weather_action
from actions.web_search        import web_search as web_search_action
from actions.docx_tools        import word_document
from actions.youtube_video     import youtube_video


# Bound on how long execute_sync waits for a tool running on the live loop.
# A hung tool must not wedge the caller forever: the ``except Exception``
# below turns any failure -- timeout included -- into a result string the
# model can read. The bound is generous because legitimate tools (dev-agent
# codegen, meeting transcription) can run for minutes; the timed-out coroutine
# is deliberately *not* cancelled -- it finishes in the background, we just
# stop blocking on it.
TOOL_EXEC_TIMEOUT_SECONDS = 300.0


class _LocalFC:
    def __init__(self, n, a, cid):
        self.name = n
        self.args = a
        self.id = cid
        self.silent_completion = True


class ToolExecutor:
    """Executes one tool call. `host` is the owning BrahmaLive instance."""

    def __init__(self, host, *, ig_gemini_reply, tool_declarations):
        self._host = host
        self._ig_gemini_reply = ig_gemini_reply
        self._tool_declarations = tool_declarations
        self._bridge_depth = 0
        self._recent_calls = []

    def execute_via_bridge(self, name: str, args: dict) -> str:
        """Run a tool on behalf of the tool bridge's call_tool meta-tool."""
        self._bridge_depth += 1
        try:
            # Prevent infinite loops
            key = (name, str(args)[:200])
            self._recent_calls.append(key)
            if len(self._recent_calls) > 50:
                self._recent_calls = self._recent_calls[-20:]
            # If same call repeated too many times, break
            if self._recent_calls.count(key) > 3:
                return f"Tool '{name}' skipped to prevent loop (repeated calls detected)."
            return self.execute_sync(name, args, call_id="call_bridge_" + name)
        finally:
            self._bridge_depth -= 1

    def execute_sync(self, name: str, args: dict, call_id: str = "call_local") -> str:
        fc = _LocalFC(name, args, call_id)
        loop = self._host._loop
        if loop and loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self.execute(fc), loop)
                fr = future.result(timeout=TOOL_EXEC_TIMEOUT_SECONDS)
                res_obj = getattr(fr, "response", {})
                return res_obj.get("result", str(res_obj))
            except Exception as e:
                return "Tool execution failed: " + str(e)
        try:
            fr = asyncio.run(self.execute(fc))
            res_obj = getattr(fr, "response", {})
            return res_obj.get("result", str(res_obj))
        except Exception as e:
            return "Tool execution failed: " + str(e)

    async def execute(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        # Discovery meta-tools are handled here, before the HUD/speech noise:
        # they are plumbing the model uses to reach unadvertised capabilities,
        # and narrating "working on search tools" to the user is just wrong.
        try:
            from core import tool_bridge

            if tool_bridge.is_meta_tool(name):
                call_id = getattr(fc, "id", None) or f"call_{name}"
                text = await tool_bridge.execute_meta(name, args)
                print(f"[BRAHMA EVO] 🧭 {name} -> {text[:200].replace(chr(10), ' ')}")
                return types.FunctionResponse(name=name, id=call_id, response={"result": text})
        except Exception as exc:
            print(f"[BRAHMA EVO] ⚠️ Meta-tool '{name}' unavailable: {exc}")

        # Architectural proxy: if this is not a meta-tool and not a known built-in,
        # try to route it through call_tool (for gateways that allow direct calls to
        # discovered tools, or as a safety net). This keeps the system backend-agnostic.
        # CRITICAL: Do NOT proxy if called via bridge path (call_bridge_*) to avoid loops
        try:
            call_id_check = getattr(fc, "id", "") or ""
            if getattr(self, "_bridge_depth", 0) > 0 or call_id_check.startswith("call_bridge_"):
                pass  # skip proxy to avoid re-entrancy loop
            else:
                from core import tool_bridge as _tb
                if not _tb.is_meta_tool(name):
                    # Don't proxy MCP tools - they need to go through normal path
                    if str(name).startswith("mcp_") or ":" in str(name) or str(name).startswith("synapse"):
                        pass
                    else:
                        # Check if it's a known tool in declarations or will be handled by the big if-elif
                        known = name in {tool.get("name") for tool in self._tool_declarations}
                        if not known:
                            try:
                                from core import tool_index as _ti
                                if _ti.get_index().get(name) is not None:
                                    known = True
                            except Exception:
                                pass
                        if not known:
                            try:
                                from core.dynamic_registry import DynamicToolRegistry
                                if DynamicToolRegistry.has_tool(name):
                                    known = True
                            except Exception:
                                pass
                        if not known:
                            print(f"[BRAHMA EVO] 🔀 Routing unknown/deferred tool '{name}' via call_tool proxy")
                            try:
                                text = await _tb.execute_meta("call_tool", {"tool_name": name, "arguments": args})
                                call_id = getattr(fc, "id", None) or f"call_{name}"
                                return types.FunctionResponse(name=name, id=call_id, response={"result": text})
                            except Exception as exc:
                                print(f"[BRAHMA EVO] ⚠️ Proxy routing failed for '{name}': {exc}")
        except Exception:
            pass

        print(f"[BRAHMA EVO] 🔧 {name}  {args}")
        self._host.speak(f"Working on {name.replace('_', ' ')}...")
        self._host.ui.set_state("THINKING")

        # Trigger Brahma Right Wing: Live Operations & Sources Telemetry
        tool_title = name.replace("_", " ").title()
        brief_query = (
            args.get("query")
            or args.get("description")
            or args.get("topic")
            or args.get("prompt")
            or args.get("title")
            or args.get("action")
            or ""
        )
        sources = []
        if "url" in args:
            sources.append(args["url"])
        if "file_path" in args:
            sources.append(Path(args["file_path"]).name)
        elif "path" in args:
            sources.append(Path(args["path"]).name)
        elif "query" in args and name in ("web_search", "flight_finder", "youtube_video"):
            sources.append(f"Query: {str(args['query'])[:28]}")

        try:
            self._host.ui.show_hud_operation(
                title=f"{tool_title.upper()} ACTIVE",
                step=f"Processing: {brief_query[:75]}" if brief_query else f"Executing {tool_title}...",
                sources=sources,
                tool=name
            )
        except Exception:
            pass

        try:
            self._host.ui.update_task_workspace(
                title=f"Running {name}",
                status=f"Executing {name}",
                output="Waiting for the tool to finish.",
                percent=45,
            )
        except Exception:
            pass
        if name == "undo":
            action = args.get("action", "undo")
            if action == "list":
                items = undo_stack.history()
                result = ("Things I can undo, most recent first:\n" +
                          "\n".join(f"  • {item}" for item in items)
                         ) if items else "I have not changed anything I can undo yet."
            else:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, undo_stack.undo_last)
            self._host.speak("Undone.")
            self._host.ui.set_state("LISTENING")
            return types.FunctionResponse(name=name, id=fc.id, response={"result": result})

        elif name == "recall_memory":
            query = args.get("query", "")
            result = search_memory(query, limit=8)
            self._host.ui.set_state("LISTENING")
            return types.FunctionResponse(name=name, id=fc.id, response={"result": result})

        elif name == "execute_protocol":
            protocol_name = args.get("protocol", "")
            res = protocols.execute(protocol_name)
            msg = res.get("result", res.get("error", "Protocol completed."))
            self._host.speak(msg)
            self._host.ui.set_state("LISTENING")
            return types.FunctionResponse(name=name, id=fc.id, response={"result": res})

        elif name == "get_sensorium_telemetry":
            snapshot = sensorium.get_snapshot()
            self._host.ui.set_state("LISTENING")
            return types.FunctionResponse(name=name, id=fc.id, response={"result": snapshot})

        elif name == "manage_local_model":
            action = args.get("action", "status")
            model_target = args.get("model_name", "")
            if action == "status":
                avail = local_brain.is_available()
                models = local_brain.list_installed_models()
                res = {
                    "server_online": avail,
                    "active_model": local_brain.default_model,
                    "local_mode_enabled": local_brain.enabled,
                    "installed_models": models,
                }
                msg = f"Local brain is {'online' if avail else 'offline'}. Installed models: {', '.join(models) if models else 'none'}."
            elif action == "pull" and model_target:
                local_brain.pull_model_async(model_target)
                msg = f"Initiated background download of local model: {model_target}."
                res = {"status": "downloading", "model": model_target}
            elif action == "toggle_offline":
                local_brain.enabled = not local_brain.enabled
                msg = f"Offline local inference mode {'engaged' if local_brain.enabled else 'disengaged'}."
                res = {"local_mode_enabled": local_brain.enabled}
            elif action == "set_active" and model_target:
                local_brain.default_model = model_target
                msg = f"Active local model set to {model_target}."
                res = {"active_model": local_brain.default_model}
            else:
                msg = "Invalid action."
                res = {"error": msg}

            self._host.speak(msg)
            self._host.ui.set_state("LISTENING")
            return types.FunctionResponse(name=name, id=fc.id, response={"result": res})

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
                try:
                    self._host.ui.finish_task_workspace("Memory saved.", "Memory updated.", 100)
                except Exception:
                    pass
            if not self._host.ui.muted:
                self._host.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "computer_settings":
                from actions.computer_settings import computer_settings as cs_run
                r = await loop.run_in_executor(None, lambda: cs_run(parameters=args, player=self._host.ui))
                result = r or "Settings updated."

            elif name == "dev_agent":
                from actions.dev_agent import dev_agent as da_run
                r = await loop.run_in_executor(None, lambda: da_run(parameters=args, player=self._host.ui, speak=self._host.speak))
                result = r or "Done."

            elif name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self._host.ui))
                result = r or f"Opened {args.get('app_name')}."
                
            elif name == "check_instagram_messages":
                self._host.ui.write_log("SYS: Checking Instagram messages...")
                if hasattr(self._host.ui, "show_hud_operation"):
                    self._host.ui.show_hud_operation("Instagram Inbox", "Fetching recent direct messages...", sources=["instagram.com"])
                from actions.instagram_mcp import get_recent_messages, InstagramService
                amount = int(args.get("amount", 5)) if args else 5
                result = await loop.run_in_executor(None, get_recent_messages, amount)
                if hasattr(self._host.ui, "show_hud_deliverable"):
                    try:
                        inbox_data = InstagramService.instance().get_inbox(amount=amount)
                        bullets = [f"@{item['sender']}: {item['last_message']}" for item in inbox_data[:4]]
                        self._host.ui.show_hud_deliverable("Instagram Inbox", summary=f"Retrieved {len(inbox_data)} recent conversations", bullets=bullets, kind="result")
                    except Exception:
                        pass

            elif name == "instagram_send_dm":
                recipient = args.get("recipient", "")
                message = args.get("message", "")
                open_browser = args.get("open_in_browser", True)
                self._host.ui.write_log(f"SYS: Sending Instagram DM to @{recipient}...")
                if hasattr(self._host.ui, "show_hud_operation"):
                    self._host.ui.show_hud_operation("Instagram DM", f"Messaging @{recipient}...", sources=["instagram.com"])
                from actions.instagram_mcp import InstagramService
                def _do_send():
                    return InstagramService.instance().send_dm(recipient, message, open_in_browser=open_browser)
                res = await loop.run_in_executor(None, _do_send)
                if hasattr(self._host.ui, "show_hud_deliverable"):
                    self._host.ui.show_hud_deliverable("Instagram DM Sent", summary=f"Direct message sent to @{res.get('recipient')}", bullets=[f"Message: {message[:60]}...", "Opened in browser for verification"], kind="result")
                result = f"Direct message sent to @{recipient}. Chat opened in browser: {res.get('browser_url')}"

            elif name == "instagram_post_photo":
                image_path = args.get("image_path", "")
                caption = args.get("caption", "")
                open_browser = args.get("open_in_browser", True)
                self._host.ui.write_log("SYS: Publishing photo to Instagram...")
                if hasattr(self._host.ui, "show_hud_operation"):
                    self._host.ui.show_hud_operation("Instagram Post", f"Uploading {Path(image_path).name}...", sources=["instagram.com"])
                from actions.instagram_mcp import InstagramService
                def _do_post():
                    return InstagramService.instance().post_photo(image_path, caption=caption, open_in_browser=open_browser)
                res = await loop.run_in_executor(None, _do_post)
                if hasattr(self._host.ui, "show_hud_deliverable"):
                    self._host.ui.show_hud_deliverable("Instagram Post Published", summary="Photo published live to Instagram!", bullets=[f"URL: {res.get('post_url')}", f"Caption: {caption[:60]}..."], kind="deliverable")
                result = f"Successfully published photo to Instagram! Post URL: {res.get('post_url')}"

            elif name == "instagram_post_reel":
                video_path = args.get("video_path", "")
                caption = args.get("caption", "")
                thumb = args.get("thumbnail_path")
                open_browser = args.get("open_in_browser", True)
                self._host.ui.write_log("SYS: Publishing Reel to Instagram...")
                if hasattr(self._host.ui, "show_hud_operation"):
                    self._host.ui.show_hud_operation("Instagram Reel", f"Uploading {Path(video_path).name}...", sources=["instagram.com"])
                from actions.instagram_mcp import InstagramService
                def _do_reel():
                    return InstagramService.instance().post_reel(video_path, caption=caption, thumbnail_path=thumb, open_in_browser=open_browser)
                res = await loop.run_in_executor(None, _do_reel)
                if hasattr(self._host.ui, "show_hud_deliverable"):
                    self._host.ui.show_hud_deliverable("Instagram Reel Published", summary="Reel published live to Instagram!", bullets=[f"URL: {res.get('reel_url')}", f"Caption: {caption[:60]}..."], kind="deliverable")
                result = f"Successfully published Reel to Instagram! Reel URL: {res.get('reel_url')}"

            elif name == "instagram_get_user_info":
                username = args.get("username", "")
                self._host.ui.write_log(f"SYS: Looking up Instagram user @{username}...")
                if hasattr(self._host.ui, "show_hud_operation"):
                    self._host.ui.show_hud_operation("Instagram Intelligence", f"Looking up @{username}...", sources=["instagram.com"])
                from actions.instagram_mcp import InstagramService
                def _do_lookup():
                    return InstagramService.instance().get_user_profile(username)
                res = await loop.run_in_executor(None, _do_lookup)
                if hasattr(self._host.ui, "show_hud_deliverable"):
                    self._host.ui.show_hud_deliverable(f"@{res.get('username')} Profile", summary=res.get("bio", "No bio"), bullets=[f"Followers: {res.get('followers', 0):,}", f"Following: {res.get('following', 0):,}", f"Posts: {res.get('posts_count', 0):,}", f"Verified: {'Yes' if res.get('is_verified') else 'No'}"], kind="result")
                result = json.dumps(res, indent=2)

            elif name == "instagram_reply":
                action = args.get("action")
                reply_text = args.get("reply_text")
                if getattr(self, "_ig_pending_thread", None):
                    thread_id = self._host._ig_pending_thread.get("thread_id")
                    username = self._host._ig_pending_thread.get("username")
                    from actions.instagram_mcp import add_auto_thread, send_direct_reply
                    if action == "take_over":
                        self._host.ui.write_log("SYS: Taking over Instagram thread via tool.")
                        add_auto_thread(thread_id)
                        message_text = self._host._ig_pending_thread.get('message')
                        def _generate_and_send():
                            try:
                                reply = self._ig_gemini_reply(username, message_text)
                                send_direct_reply(thread_id, reply)
                            except Exception as e:
                                print(f"Error taking over thread: {e}")
                        threading.Thread(target=_generate_and_send, daemon=True).start()
                        result = f"Successfully took over the chat with @{username}. The backend will now automatically reply to them."
                    else:
                        self._host.ui.write_log(f"SYS: Sending manual reply to @{username}.")
                        send_direct_reply(thread_id, reply_text)
                        result = f"Successfully sent manual reply to @{username} and opened thread in browser."
                        
                    self._host._ig_reply_mode = False
                    self._host._ig_pending_thread = None
                else:
                    recipient = args.get("recipient") or args.get("username")
                    from actions.instagram_mcp import InstagramService, add_auto_thread
                    thread_id = None
                    if not recipient:
                        try:
                            inbox = InstagramService.instance().get_inbox(amount=1)
                            if inbox:
                                recipient = inbox[0].get("sender")
                                thread_id = inbox[0].get("thread_id")
                        except Exception:
                            pass

                    if recipient:
                        if action == "take_over":
                            add_auto_thread(thread_id or recipient)
                            result = f"Successfully took over the chat with @{recipient}. Brahma Evo will now automatically reply."
                        else:
                            res = InstagramService.instance().send_dm(recipient, reply_text, open_in_browser=True)
                            result = f"Sent reply to @{recipient}: '{reply_text}'. Thread opened in browser."
                        self._host._ig_reply_mode = False
                    else:
                        result = "Could not find a recent conversation to reply to. Please specify who you want to message."

            elif name == "system_manager":
                from actions.system_manager import run as sm_run
                r = await loop.run_in_executor(None, lambda: sm_run(parameters=args, player=self._host.ui))
                result = r or "System status retrieved."

            elif name == "background_monitor":
                from actions.background_monitor import run as bm_run
                r = await loop.run_in_executor(None, lambda: bm_run(parameters=args, player=self._host.ui))
                result = r or "Done."

            elif name == "clipboard_processor":
                from actions.clipboard_processor import process_clipboard
                r = await loop.run_in_executor(None, lambda: process_clipboard(parameters=args, player=self._host.ui))
                result = r or "Clipboard read."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self._host.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self._host.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self._host.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self._host.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self._host.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self._host.ui))
                result = r or "Done."
            elif name == "file_processor":
                if not args.get("file_path") and self._host.ui.current_file:
                    args["file_path"] = self._host.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self._host.ui, speak=self._host.speak)
                )
                result = r or "Done."

            elif name == "presentation_builder":
                if not args.get("slides") and not args.get("outline"):
                    from actions.office_generator import generate_presentation_from_prompt
                    topic = args.get("topic") or args.get("title") or "Presentation"
                    r = await loop.run_in_executor(
                        None,
                        lambda: generate_presentation_from_prompt(topic, player=self._host.ui, speak=self._host.speak)
                    )
                else:
                    r = await loop.run_in_executor(
                        None,
                        lambda: create_presentation(parameters=args, player=self._host.ui)
                    )
                result = r or "Presentation created."

            elif name == "spreadsheet_builder":
                if not args.get("worksheets") and not args.get("sheets"):
                    from actions.office_generator import generate_spreadsheet_from_prompt
                    topic = args.get("topic") or args.get("title") or "Spreadsheet"
                    r = await loop.run_in_executor(
                        None,
                        lambda: generate_spreadsheet_from_prompt(topic, player=self._host.ui, speak=self._host.speak)
                    )
                else:
                    r = await loop.run_in_executor(
                        None,
                        lambda: create_spreadsheet(parameters=args, player=self._host.ui)
                    )
                result = r or "Spreadsheet created."


            elif name == "word_document":
                if not args.get("file_path") and self._host.ui.current_file:
                    current_file = Path(self._host.ui.current_file)
                    if current_file.suffix.lower() == ".docx":
                        args["file_path"] = self._host.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: word_document(parameters=args, player=self._host.ui, speak=self._host.speak)
                )
                result = r or "Word document handled."

            elif name == "pdf_document":
                r = await loop.run_in_executor(
                    None,
                    lambda: create_pdf(parameters=args, player=self._host.ui)
                )
                result = r or "PDF created."

            elif name == "screen_process":
                if hasattr(self, "set_scanning"):
                    self._host.ui.set_scanning(True, "SCANNING SCREEN")
                threading.Thread(
                    target=screen_process,
                    kwargs={
                        "parameters": args,
                        "response": None,
                        "player": self._host.ui,
                        "session_memory": None,
                    },
                    daemon=True,
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self._host.ui))
                result = r or "Done."

            elif name == "smart_home_control":
                command_text = str(args.get("command") or "").strip()
                r = await loop.run_in_executor(None, lambda: self._host._smart_home.execute_command(command_text))
                result = str((r or {}).get("detail") or "Smart-home command completed.")

            elif name in ("smart_organizer", "desktop_organizer"):
                from actions.desktop_organizer_mcp import smart_organizer
                r = await loop.run_in_executor(None, lambda: smart_organizer(parameters=args, player=self._host.ui, speak=self._host.speak))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self._host.ui))
                result = r or "Done."

            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id  = get_queue().submit(
                    goal=args.get("goal", ""),
                    priority=priority,
                    speak=self._host.speak,
                    player=self._host.ui
                )
                result   = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self._host.ui))
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self._host.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self._host.ui, speak=self._host.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self._host.ui))
                result = r or "Done."
            elif name == "circuit_assembler":
                from actions.circuit_assembler import circuit_assembler
                r = await loop.run_in_executor(
                    None,
                    lambda: circuit_assembler(parameters=args, player=self._host.ui, speak=self._host.speak)
                )
                result = r.get("summary", "Circuit schematic ready.") if isinstance(r, dict) else (r or "Circuit schematic ready.")
            elif name == "geospatial_globe":
                from core.globe_window import GlobeWindow
                globe = GlobeWindow.get_instance(parent=self._host.ui._win)
                action = (args.get("action") or "open").lower().strip()
                location = args.get("location") or "current"
                if action == "open":
                    globe.open_globe(location if args.get("location") else None)
                    result = "Opened the interactive Brahma map."
                elif action == "route":
                    r = await loop.run_in_executor(None, lambda: globe.show_route(args.get("origin", ""), args.get("destination", "")))
                    result = f"Flight route: {r.get('origin')} to {r.get('destination')}, {r.get('distance_km')} km, about {r.get('flight_time')}."
                elif action == "drive":
                    r = await loop.run_in_executor(None, lambda: globe.show_driving_route(args.get("origin", ""), args.get("destination", "")))
                    result = f"Driving route: {r.get('origin')} to {r.get('destination')}, {r.get('distance_km')} km, {r.get('duration_str')}."
                elif action in ("location", "fly_to"):
                    r = await loop.run_in_executor(None, lambda: globe.show_location(location) if action == "location" else globe.fly_to(location))
                    result = f"Map focused on {r.get('location', location)}."
                elif action == "weather":
                    r = await loop.run_in_executor(None, lambda: globe.show_weather(location))
                    result = f"Weather for {r.get('location')}: {r.get('weather')}."
                elif action == "flights":
                    r = await loop.run_in_executor(None, lambda: globe.show_live_flights(args.get("location")))
                    result = f"Showing {len(r)} live aircraft on the map."
                elif action == "iss":
                    r = await loop.run_in_executor(None, globe.show_iss_tracker)
                    result = f"ISS location: {r.get('lat')}, {r.get('lon')}; altitude {r.get('altitude_km')} km."
                elif action == "earthquakes":
                    r = await loop.run_in_executor(None, lambda: globe.show_earthquakes(float(args.get("min_magnitude", 2.5))))
                    result = f"Showing {len(r)} recent earthquakes on the map."
                elif action == "nearby":
                    r = await loop.run_in_executor(None, lambda: globe.show_nearby(args.get("query") or "hospitals", args.get("location")))
                    result = f"Found {len(r)} nearby places."
                elif action == "radar":
                    enabled = bool(args.get("enable", True))
                    await loop.run_in_executor(None, lambda: globe.toggle_weather_radar(enabled))
                    result = f"Weather radar {'enabled' if enabled else 'disabled'}."
                else:
                    result = f"Unsupported map action: {action}."
            elif name == "call_screening":
                action = (args.get("action") or "start").lower().strip()
                from actions.call_assistant import hang_up_active_call, start_call_proxy, take_over_active_call
                if action == "take_over":
                    take_over_active_call()
                    result = "Taking over the screened call."
                elif action == "hang_up":
                    hang_up_active_call()
                    result = "Ended the screened call."
                elif action == "start":
                    event = {"title": args.get("caller") or "Incoming call", "app": args.get("app") or "Phone / Call"}
                    from core.confirm import request
                    result = request(
                        "start-call-screening",
                        "Answer this call as Brahma Evo",
                        f"Brahma will answer {event['title']} in {event['app']}, listen to the caller, and prepare a transcript and summary.",
                        lambda: (start_call_proxy(event, ui=self._host.ui, speak_fn=self._host.speak) and "Call screening started.")
                    )
                    self._host.ui.set_state("LISTENING")
                    return types.FunctionResponse(id=fc.id, name=name, response={"result": result})
                else:
                    result = "Choose start, take_over, or hang_up."
            elif name == "skill_forge":
                action = (args.get("action") or "forge").lower().strip()
                from core.dynamic_registry import DynamicToolRegistry
                if action == "list":
                    skills = DynamicToolRegistry.list_skills()
                    result = "Installed skills: " + (", ".join(skill["name"] for skill in skills) or "none")
                elif action == "forge":
                    goal = (args.get("goal") or "").strip()
                    if not goal:
                        result = "Describe the capability you want Brahma to learn."
                    else:
                        threading.Thread(
                            target=self._host._forge_skill,
                            args=(goal, args.get("skill_name")),
                            daemon=True,
                            name="skill-forge",
                        ).start()
                        result = f"Started creating the requested skill: {goal}"
                        self._host.ui.set_state("LISTENING")
                        return types.FunctionResponse(id=fc.id, name=name, response={"result": result})
                else:
                    result = "Choose forge or list."
            elif name == "dynamic_skill":
                action = (args.get("action") or "list").lower().strip()
                from core.dynamic_registry import DynamicToolRegistry
                if action == "list":
                    skills = DynamicToolRegistry.list_skills()
                    result = "Installed skills: " + (", ".join(skill["name"] for skill in skills) or "none")
                elif action == "run":
                    skill_name = (args.get("skill_name") or "").strip()
                    if not DynamicToolRegistry.has_tool(skill_name):
                        result = f"No active skill named '{skill_name}'."
                    else:
                        run_res = await loop.run_in_executor(
                            None,
                            lambda: DynamicToolRegistry.execute_sync(skill_name, args.get("arguments") or args or {})
                        )
                        if isinstance(run_res, dict):
                            out_text = str(run_res.get("summary") or run_res.get("output") or run_res.get("text") or run_res).strip()
                        else:
                            out_text = str(run_res).strip()
                        self._host.ui.write_log(f"Brahma Evo [{skill_name}]:\n{out_text}")
                        result = out_text
                else:
                    result = "Choose list or run."
            elif name and name not in {tool.get("name") for tool in self._tool_declarations}:
                from core.dynamic_registry import DynamicToolRegistry
                if DynamicToolRegistry.has_tool(name):
                    run_res = await loop.run_in_executor(
                        None,
                        lambda: DynamicToolRegistry.execute_sync(name, args or {})
                    )
                    if isinstance(run_res, dict):
                        out_text = str(run_res.get("summary") or run_res.get("output") or run_res.get("text") or run_res).strip()
                    else:
                        out_text = str(run_res).strip()
                    self._host.ui.write_log(f"Brahma Evo [{name}]:\n{out_text}")
                    result = out_text
                else:
                    result = f"Unknown tool: {name}"
            elif name == "connect_list_devices":
                r = await loop.run_in_executor(None, lambda: connect_list_devices(parameters=args, player=self._host.ui))
                result = r or "Done."
            elif name == "connect_get_device":
                r = await loop.run_in_executor(None, lambda: connect_get_device(parameters=args, player=self._host.ui))
                result = r or "Done."
            elif name == "connect_get_capabilities":
                r = await loop.run_in_executor(None, lambda: connect_get_capabilities(parameters=args, player=self._host.ui))
                result = r or "Done."
            elif name == "connect_execute":
                r = await loop.run_in_executor(None, lambda: connect_execute(parameters=args, player=self._host.ui))
                result = r or "Done."
            elif name == "connect_pair_device":
                r = await loop.run_in_executor(None, lambda: connect_pair_device(parameters=args, player=self._host.ui))
                result = r or "Done."
            elif name == "connect_disconnect_device":
                r = await loop.run_in_executor(None, lambda: connect_disconnect_device(parameters=args, player=self._host.ui))
                result = r or "Done."
            elif name in ("spotify_controller", "spotify", "music"):
                from actions.spotify_controller import spotify_controller
                r = await loop.run_in_executor(None, lambda: spotify_controller(parameters=args, player=self._host.ui, speak=self._host.speak))
                result = r or "Done."
            elif name in ("calendar_scheduler", "calendar", "schedule"):
                from actions.calendar_scheduler import calendar_scheduler
                r = await loop.run_in_executor(None, lambda: calendar_scheduler(parameters=args, player=self._host.ui, speak=self._host.speak))
                result = r or "Done."
            elif name in ("google_workspace", "gmail", "google_calendar", "google_drive", "workspace") or name.startswith("workspace_"):
                from actions.google_workspace_mcp import google_workspace
                p = dict(args or {})
                if name.startswith("workspace_"):
                    parts = name.split("_", 2)
                    if len(parts) > 1:
                        p.setdefault("service", parts[1])
                    if len(parts) > 2:
                        p.setdefault("action", parts[2])
                r = await loop.run_in_executor(None, lambda: google_workspace(parameters=p, player=self._host.ui, speak=self._host.speak))
                result = r or "Google Workspace task completed."
            elif name in ("system_diagnostics", "diagnostics", "os_hardware", "hardware_control", "ram_hogs", "kill_process", "brightness_control"):
                from actions.system_diagnostics_mcp import system_diagnostics
                p = dict(args or {})
                if name == "ram_hogs":
                    p.setdefault("action", "ram_hogs")
                elif name == "kill_process":
                    p.setdefault("action", "kill")
                elif name == "brightness_control":
                    p.setdefault("action", "brightness")
                r = await loop.run_in_executor(None, lambda: system_diagnostics(parameters=p, player=self._host.ui, speak=self._host.speak))
                result = r or "Diagnostics completed."
            elif name in ("daily_briefing", "briefing"):
                from actions.daily_briefing import daily_briefing
                r = await loop.run_in_executor(None, lambda: daily_briefing(parameters=args, player=self._host.ui, speak=self._host.speak))
                result = r or "Delivered daily briefing."
            elif name == "unlock_device":
                from actions.unlock_device import unlock_device
                r = await loop.run_in_executor(None, lambda: unlock_device(parameters=args, player=self._host.ui))
                result = r or "Done."
            elif name in ("auto_heal", "self_patch", "rollback"):
                from actions.auto_heal_engine import auto_heal
                p = dict(args or {})
                if name == "rollback":
                    p.setdefault("action", "rollback")
                r = await loop.run_in_executor(None, lambda: auto_heal(parameters=p, player=self._host.ui, speak=self._host.speak))
                result = r or "Auto-heal completed."
            elif name in ("calorie_counter", "nutrition_scan", "food_analysis"):
                from actions.calorie_counter import calorie_counter
                r = await loop.run_in_executor(None, lambda: calorie_counter(parameters=args, player=self._host.ui, speak=self._host.speak))
                result = r or "Nutrition analysis complete."
            elif name in ("pushup_counter", "workout_tracker", "rep_counter"):
                from actions.pushup_counter import pushup_counter
                r = await loop.run_in_executor(None, lambda: pushup_counter(parameters=args, player=self._host.ui, speak=self._host.speak))
                result = r or "Workout session complete."
            elif name in ("upload_video", "video_publisher", "publish_video"):
                from actions.upload_video import upload_video
                r = await loop.run_in_executor(None, lambda: upload_video(parameters=args, player=self._host.ui, speak=self._host.speak))
                result = r or "Video publishing ready."
            elif name == "shutdown_brahma":
                self._host.ui.write_log("SYS: Shutdown requested.")
                self._host.speak("Goodbye, sir.")

                def _shutdown():
                    import time, os
                    time.sleep(1)
                    os._exit(0)

                threading.Thread(target=_shutdown, daemon=True).start()
            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            tb_str = traceback.format_exc()
            traceback.print_exc()
            try:
                from actions.auto_heal_engine import AutoHealEngine
                AutoHealEngine.record_last_error(tb_str)
            except Exception:
                pass
            self._host.speak_error(name, e)

        try:
            if not getattr(fc, "silent_completion", False):
                self._host.speak(f"{name.replace('_', ' ')} completed.")
            self._host.ui.finish_task_workspace(result, "Task completed.", 100)
        except Exception:
            pass

        # Trigger Brahma Left Wing: Final Deliverables & Results
        try:
            import re
            file_match = re.search(r'([A-Za-z]:\\[^\s"\'<>`\r\n]+\.(?:pdf|docx|xlsx|pptx|png|jpg|mp4|py|html|json|txt))', str(result))
            detected_file = file_match.group(1).strip() if file_match else None
            if not detected_file and "file_path" in args:
                p_cand = Path(args["file_path"])
                if p_cand.exists():
                    detected_file = str(p_cand.resolve())

            res_str = str(result)
            bullets = []
            for l in res_str.splitlines():
                cl = l.strip()
                if (cl.startswith(("-", "*", "•", ">")) or cl.startswith(tuple("123456789."))) and len(cl) > 3:
                    bullets.append(cl.lstrip("-*•>0123456789. ").strip())
                elif len(cl) > 15 and not cl.startswith("#") and len(bullets) < 4:
                    bullets.append(cl)

            self._host.ui.show_hud_deliverable(
                title=f"{tool_title.upper()} DELIVERABLE",
                summary=res_str[:160] if not bullets else "",
                bullets=bullets[:5],
                file_path=detected_file,
                kind=name
            )
        except Exception:
            pass

        tool_voice = self._host._connect_tool_voice(name, result)
        if tool_voice:
            try:
                self._host.ui.write_log(f"Brahma Evo: {tool_voice}")
            except Exception:
                pass
            try:
                self._host.speak(tool_voice)
            except Exception:
                pass

        if not self._host.ui.muted:
            self._host.ui.set_state("LISTENING")

        print(f"[BRAHMA EVO] 📤 {name} → {str(result)[:80]}")

        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

__all__ = ["ToolExecutor"]
