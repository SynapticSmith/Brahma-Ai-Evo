"""Tool-call execution, lifted out of main.py's BrahmaLive (P4-19 seam).

Behavior-preserving move: BrahmaLive keeps thin delegating wrappers and hands
this executor a host reference plus the two main.py symbols it needs
(`_ig_gemini_reply`, TOOL_DECLARATIONS), so there is no circular import.
"""
from __future__ import annotations

import asyncio
import threading
import traceback

from google.genai import types

from actions.browser_control   import browser_control
from actions.computer_control  import computer_control
from actions.computer_settings import computer_settings
from actions.desktop           import desktop_control
from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.game_updater      import game_updater
from core.local_brain import local_brain
from core import desktop_host
from actions.open_app          import open_app
from core.protocols import protocols
from actions.screen_processor  import screen_process
from memory.memory_manager import search_memory
from core.sensorium import sensorium
from core import undo as undo_stack
from actions.weather_report    import weather_action
from actions.web_search        import web_search as web_search_action
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
        # ADR-019: the host renders HUD feedback for the desktop specs the
        # runtime dispatches, and this executor hands the same bundle to the
        # adapter. Built here because BrahmaLive exists only after main()'s
        # runner starts; every dispatch constructs this executor first.
        from core import desktop_tools

        self._desktop = desktop_tools.attach_host(host)

    def execute_via_bridge(self, name: str, args: dict) -> str:
        """Run a tool on behalf of the tool bridge's call_tool meta-tool."""
        self._bridge_depth += 1
        try:
            # Prevent infinite loops
            key = (name, str(args)[:200])
            self._recent_calls.append(key)
            if len(self._recent_calls) > 100:
                self._recent_calls = self._recent_calls[-30:]
            # If same call repeated too many times, break
            if self._recent_calls.count(key) > 5:
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

        # Harness tools (file/edit/glob/grep/shell) are registered in
        # ``agent.spec`` and dispatched through the permission-aware runtime.
        # They run in a worker thread: an approval can block for as long as
        # the user takes to click, and this coroutine drives the live audio
        # session.
        try:
            from agent import code_tools  # noqa: F401  (import registers specs)
            from agent import runtime as agent_runtime
            from agent.spec import get as get_spec

            spec = get_spec(name)
            if spec is not None:
                call_id = getattr(fc, "id", None) or f"call_{name}"
                text = await asyncio.to_thread(
                    agent_runtime.execute,
                    name,
                    args,
                    call_id=call_id,
                    extra={"host": self._desktop.host},
                )
                print(f"[BRAHMA EVO] 🧰 {name} -> {str(text)[:200].replace(chr(10), ' ')}")
                payload = {"result": text}
                if spec.silent:
                    payload["silent"] = True
                return types.FunctionResponse(name=name, id=call_id, response=payload)
        except Exception as exc:
            print(f"[BRAHMA EVO] ⚠️ Harness tool '{name}' unavailable: {exc}")

        # Discovery meta-tools are handled here, before the HUD/speech noise:
        # they are plumbing the model uses to reach unadvertised capabilities,
        # and narrating "working on search tools" to the user is just wrong.
        try:
            from core import tool_bridge

            if tool_bridge.is_meta_tool(name):
                call_id = getattr(fc, "id", None) or f"call_{name}"
                # Check for loops
                if getattr(self, "_bridge_depth", 0) == 0 and not call_id.startswith("call_bridge_"):
                    # This is a direct call to meta-tool from Live - fine
                    pass
                text = await tool_bridge.execute_meta(name, args)
                print(f"[BRAHMA EVO] 🧭 {name} -> {text[:200].replace(chr(10), ' ')}")
                return types.FunctionResponse(name=name, id=call_id, response={"result": text})
        except Exception as exc:
            print(f"[BRAHMA EVO] ⚠️ Meta-tool '{name}' unavailable: {exc}")

        # Architectural proxy disabled to prevent loops - rely on prompting and call_tool flow

        desktop_host.present_start(self._desktop.host, name, args)
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

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self._host.ui))
                result = r or "Done."

            elif name == "agent_task":
                # The legacy planner/executor was removed: it bypassed
                # AgentRuntime entirely. Anything still calling this name gets a
                # pointer at the harness instead of a second execution path.
                result = (
                    "agent_task is retired. Use the agent harness tools "
                    "(run_command, read_file, write_file, run_subagent) instead."
                )

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

        desktop_host.present_end(
            self._desktop.host,
            name,
            args,
            result,
        )

        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

__all__ = ["ToolExecutor"]
