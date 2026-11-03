"""Desktop actions registered as ``ToolSpec``s (ADR-019, waves 1-3).

The voice and text doors used to dispatch every native capability through the
``elif`` chain in :mod:`core.tool_exec`, which is the one execution path with no
permission check. This module publishes the migrated native actions as
ordinary specs instead, so a call goes through
:meth:`agent.runtime.AgentRuntime.execute` -- policy, approval, then handler --
like every other tool.

Two rules keep the migration honest:

* **Registration is host-side.** ``agent/`` never imports this module, never
  learns an action name, and stays importable with only pytest installed.
* **Adapters, not rewrites.** Each handler is a thin wrapper over the existing
  ``actions/`` function, with the ``player``/``speak`` keyword arguments it
  already accepts. The 22 module signatures do not change.

Wave 1 is the unambiguous slice: the four document producers (``write``) plus
the process-spawning/self-mutating actions (``exec``). Wave 2 adds the
per-action classified file/organizer specs (``read`` vs ``write``), the
``device`` slice (``unlock_device``, ``connect_*``), the ``network`` slice
(``send_message``, Instagram), and ``save_memory`` (``silent=True``). Wave 3
migrates the remaining native declarations and aliases, retiring the legacy
``elif`` chain in :mod:`core.tool_exec`.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from agent.spec import ToolSpec, get as get_spec
from agent.spec import lowercase_schema
from agent.spec import register as register_spec

from . import desktop_host

#: ``name -> (tool_class, requires_approval)``. The class is what the policy
#: reasons about; ``requires_approval`` is for actions that ask *every* time
#: even after "always allow", because undoing them needs a human and a backup.
WAVE1: Dict[str, Tuple[str, bool]] = {
    "pdf_document": ("write", False),
    "word_document": ("write", False),
    "presentation_builder": ("write", False),
    "spreadsheet_builder": ("write", False),
    # schtasks: a persisted task that outlives the session.
    "reminder": ("exec", False),
    # Patches its own source tree behind a backup; the runtime never remembers
    # an approval for these (see AgentRuntime.execute).
    "auto_heal": ("exec", True),
    "self_patch": ("exec", True),
    "rollback": ("exec", True),
    "shutdown_brahma": ("exec", True),
}

#: Wave 2: mixed-verb file/organizer tools (declared ``write`` so the static
#: fallback is fail-safe and read-only subagent defaults exclude them; the
#: per-call classifier narrows read-only verbs to ``read``), ``save_memory``
#: (``silent=True``), ``device`` actions, and ``network`` messaging/social.
WAVE2: Dict[str, Tuple[str, bool]] = {
    "file_controller": ("write", False),
    "smart_organizer": ("write", False),
    "desktop_organizer": ("write", False),
    "save_memory": ("write", False),
    "unlock_device": ("device", False),
    "connect_list_devices": ("device", False),
    "connect_get_device": ("device", False),
    "connect_get_capabilities": ("device", False),
    "connect_execute": ("device", False),
    "connect_pair_device": ("device", False),
    "connect_disconnect_device": ("device", False),
    "send_message": ("network", False),
    "check_instagram_messages": ("network", False),
    "instagram_send_dm": ("network", False),
    "instagram_post_photo": ("network", False),
    "instagram_post_reel": ("network", False),
    "instagram_get_user_info": ("network", False),
    "instagram_reply": ("network", False),
}

#: Wave 3: remaining native declarations from ``main.TOOL_DECLARATIONS``, their
#: legacy aliases, and the retired ``agent_task`` pointer.
WAVE3: Dict[str, Tuple[str, bool]] = {
    "undo": ("write", False),
    "recall_memory": ("read", False),
    "execute_protocol": ("exec", False),
    "get_sensorium_telemetry": ("read", False),
    "manage_local_model": ("write", False),
    "dev_agent": ("exec", False),
    "background_monitor": ("exec", False),
    "system_manager": ("exec", False),
    "clipboard_processor": ("read", False),
    "open_app": ("exec", False),
    "web_search": ("network", False),
    "weather_report": ("network", False),
    "youtube_video": ("network", False),
    "screen_process": ("read", False),
    "computer_settings": ("device", False),
    "smart_home_control": ("device", False),
    "browser_control": ("network", False),
    "desktop_control": ("device", False),
    "computer_control": ("device", False),
    "game_updater": ("exec", False),
    "flight_finder": ("network", False),
    "file_processor": ("read", False),
    "spotify_controller": ("network", False),
    "spotify": ("network", False),
    "music": ("network", False),
    "calendar_scheduler": ("write", False),
    "calendar": ("write", False),
    "schedule": ("write", False),
    "daily_briefing": ("network", False),
    "briefing": ("network", False),
    "google_workspace": ("network", False),
    "gmail": ("network", False),
    "google_calendar": ("network", False),
    "google_drive": ("network", False),
    "workspace": ("network", False),
    "system_diagnostics": ("exec", False),
    "diagnostics": ("exec", False),
    "os_hardware": ("exec", False),
    "hardware_control": ("exec", False),
    "ram_hogs": ("read", False),
    "kill_process": ("exec", False),
    "brightness_control": ("device", False),
    "circuit_assembler": ("write", False),
    "geospatial_globe": ("network", False),
    "call_screening": ("device", False),
    "skill_forge": ("write", False),
    "dynamic_skill": ("exec", False),
    "calorie_counter": ("network", False),
    "nutrition_scan": ("network", False),
    "food_analysis": ("network", False),
    "pushup_counter": ("device", False),
    "workout_tracker": ("device", False),
    "rep_counter": ("device", False),
    "upload_video": ("network", False),
    "video_publisher": ("network", False),
    "publish_video": ("network", False),
    "agent_task": ("read", False),
    "generate_image": ("network", False),
    "create_image": ("network", False),
    "text_to_image": ("network", False),
    "draw_image": ("network", False),
    "ai_art": ("network", False),
    "image_gen": ("network", False),
    "reverse_engineer": ("network", False),
    "game_sentinel": ("exec", False),
    "study_guard": ("device", False),
    "net_sentinel": ("network", False),
    "space_cleaner": ("write", False),
}

#: Names the model may use that are not their own declaration. ``rollback``
#: also forces its action; ``self_patch`` is the plain auto-heal entry point;
#: ``desktop_organizer`` is the legacy alias for ``smart_organizer``.
ALIASES: Dict[str, str] = {
    "self_patch": "auto_heal",
    "rollback": "auto_heal",
    "desktop_organizer": "smart_organizer",
    "spotify": "spotify_controller",
    "music": "spotify_controller",
    "calendar": "calendar_scheduler",
    "schedule": "calendar_scheduler",
    "briefing": "daily_briefing",
    "gmail": "google_workspace",
    "google_calendar": "google_workspace",
    "google_drive": "google_workspace",
    "workspace": "google_workspace",
    "diagnostics": "system_diagnostics",
    "os_hardware": "system_diagnostics",
    "hardware_control": "system_diagnostics",
    "ram_hogs": "system_diagnostics",
    "kill_process": "system_diagnostics",
    "brightness_control": "system_diagnostics",
    "nutrition_scan": "calorie_counter",
    "food_analysis": "calorie_counter",
    "workout_tracker": "pushup_counter",
    "rep_counter": "pushup_counter",
    "video_publisher": "upload_video",
    "publish_video": "upload_video",
    "create_image": "generate_image",
    "text_to_image": "generate_image",
    "draw_image": "generate_image",
    "ai_art": "generate_image",
    "image_gen": "generate_image",
}

#: Read-only verbs for ``file_controller``. Any other (or missing/unknown)
#: action classifies as ``write`` so new or mistyped verbs fail safe.
FILE_CONTROLLER_READ_ACTIONS = frozenset(
    {
        "list",
        "read",
        "find",
        "largest",
        "disk_usage",
        "preview_organize",
        "organize_preview",
        "preview",
        "find_duplicates",
        "duplicates",
        "dupes",
        "info",
    }
)

#: Read-only verbs for ``smart_organizer`` / ``desktop_organizer``. Note that
#: ``smart_organizer`` defaults an omitted ``action`` to ``"preview"`` in its
#: implementation, but the classifier requires an explicit read verb or falls
#: back to ``write`` unless ``action`` is omitted or in this set.
SMART_ORGANIZER_READ_ACTIONS = frozenset(
    {
        "preview",
        "dry_run",
        "dryrun",
        "inspect",
        "find_duplicates",
        "duplicates",
        "dupes",
    }
)

CALENDAR_READ_ACTIONS = frozenset(
    {
        "list_events",
        "check_day",
        "get_upcoming",
        "list",
        "check",
        "upcoming",
    }
)

DIAGNOSTICS_READ_ACTIONS = frozenset(
    {
        "",
        "status",
        "ram_hogs",
        "ram",
        "memory",
        "top_ram",
        "cpu_hogs",
        "cpu",
        "processor",
        "battery",
        "power",
        "battery_health",
        "disk",
        "storage",
        "drives",
    }
)

DIAGNOSTICS_BRIGHTNESS_ACTIONS = frozenset(
    {
        "brightness",
        "set_brightness",
        "dim",
        "brighten",
    }
)


def _classify_file_controller(args: Mapping[str, Any]) -> str:
    action = str((args or {}).get("action") or "").strip().lower()
    if action in FILE_CONTROLLER_READ_ACTIONS:
        return "read"
    return "write"


def _classify_smart_organizer(args: Mapping[str, Any]) -> str:
    action = str((args or {}).get("action") or "").strip().lower()
    if action in SMART_ORGANIZER_READ_ACTIONS:
        return "read"
    return "write"


def _classify_undo(args: Mapping[str, Any]) -> str:
    action = str((args or {}).get("action") or "undo").strip().lower()
    if action == "list":
        return "read"
    return "write"


def _classify_manage_local_model(args: Mapping[str, Any]) -> str:
    action = str((args or {}).get("action") or "status").strip().lower()
    if action == "status":
        return "read"
    if action == "pull":
        return "network"
    return "write"


def _classify_system_manager(args: Mapping[str, Any]) -> str:
    action = str((args or {}).get("action") or "status").strip().lower()
    if action == "kill":
        return "exec"
    return "read"


def _classify_calendar(args: Mapping[str, Any]) -> str:
    action = str((args or {}).get("action") or "").strip().lower()
    if action in CALENDAR_READ_ACTIONS:
        return "read"
    return "write"


def _classify_system_diagnostics(default_action: str = "status"):
    def classify(args: Mapping[str, Any]) -> str:
        raw = (args or {}).get("action") or (args or {}).get("command") or default_action
        action = str(raw).strip().lower()
        if action in DIAGNOSTICS_BRIGHTNESS_ACTIONS:
            if (args or {}).get("level") is None and (args or {}).get("value") is None:
                return "read"
            return "device"
        if action in DIAGNOSTICS_READ_ACTIONS:
            return "read"
        return "exec"

    return classify


def _classify_skill_forge(args: Mapping[str, Any]) -> str:
    action = str((args or {}).get("action") or "forge").strip().lower()
    if action == "list":
        return "read"
    return "write"


def _classify_dynamic_skill(args: Mapping[str, Any]) -> str:
    action = str((args or {}).get("action") or "list").strip().lower()
    if action == "list":
        return "read"
    return "exec"




def _classify_reverse_engineer(args: Mapping[str, Any]) -> str:
    action = str((args or {}).get("action") or "analyze").strip().lower()
    if action == "remake":
        return "write"
    return "network"


def _classify_space_cleaner(args: Mapping[str, Any]) -> str:
    action = str((args or {}).get("action") or "scan").strip().lower()
    if action in ("purge", "clean"):
        return "write"
    return "read"


def _classify_game_sentinel(args: Mapping[str, Any]) -> str:
    action = str((args or {}).get("action") or "clean_ram").strip().lower()
    if action in ("status", "telemetry", "info"):
        return "read"
    return "exec"


def _classify_net_sentinel(args: Mapping[str, Any]) -> str:
    action = str((args or {}).get("action") or "scan").strip().lower()
    if action in ("wake_on_lan", "wol"):
        return "device"
    return "network"


def _classify_study_guard(args: Mapping[str, Any]) -> str:
    action = str((args or {}).get("action") or "status").strip().lower()
    if action in ("status", "telemetry"):
        return "read"
    return "device"

CLASSIFIERS: Dict[str, Callable[[Mapping[str, Any]], str]] = {
    "file_controller": _classify_file_controller,
    "smart_organizer": _classify_smart_organizer,
    "desktop_organizer": _classify_smart_organizer,
    "undo": _classify_undo,
    "manage_local_model": _classify_manage_local_model,
    "system_manager": _classify_system_manager,
    "calendar_scheduler": _classify_calendar,
    "calendar": _classify_calendar,
    "schedule": _classify_calendar,
    "system_diagnostics": _classify_system_diagnostics("status"),
    "diagnostics": _classify_system_diagnostics("status"),
    "os_hardware": _classify_system_diagnostics("status"),
    "hardware_control": _classify_system_diagnostics("status"),
    "ram_hogs": _classify_system_diagnostics("ram_hogs"),
    "kill_process": _classify_system_diagnostics("kill"),
    "brightness_control": _classify_system_diagnostics("brightness"),
    "skill_forge": _classify_skill_forge,
    "dynamic_skill": _classify_dynamic_skill,
    "reverse_engineer": _classify_reverse_engineer,
    "space_cleaner": _classify_space_cleaner,
    "game_sentinel": _classify_game_sentinel,
    "net_sentinel": _classify_net_sentinel,
    "study_guard": _classify_study_guard,
}

SILENT_TOOLS = frozenset({"save_memory"})

#: Tag every spec carries, so ``host names`` and the event sink agree without a
#: second registry to keep in sync.
DESKTOP_TAG = "desktop"


def _declarations(source: Iterable[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    return {
        str(item.get("name")): item
        for item in source or ()
        if isinstance(item, Mapping) and item.get("name")
    }


def _empty_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {}}


# --------------------------------------------------------------------------- #
# Adapters: (args, context) -> str, the only signature a ToolSpec handler has
# --------------------------------------------------------------------------- #


def _host_for(context: Any) -> Optional[desktop_host.HostServices]:
    return desktop_host.require_host(context)


def _pdf_document(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.pdf_tools import create_pdf

    return create_pdf(parameters=args, player=host.ui) or "PDF created."


def _word_document(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    # The file drop area is GUI state, so it is read through the host instead of
    # a module global. Only a .docx drop is an implicit target.
    current = host.ui.current_file
    if not args.get("file_path") and current:
        from pathlib import Path

        if Path(str(current)).suffix.lower() == ".docx":
            args["file_path"] = current
    from actions.docx_tools import word_document

    return (
        word_document(parameters=args, player=host.ui, speak=host.speak)
        or "Word document handled."
    )


def _presentation_builder(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    if not args.get("slides") and not args.get("outline"):
        from actions.office_generator import generate_presentation_from_prompt

        topic = args.get("topic") or args.get("title") or "Presentation"
        return (
            generate_presentation_from_prompt(topic, player=host.ui, speak=host.speak)
            or "Presentation created."
        )
    from actions.office_builder import create_presentation

    return create_presentation(parameters=args, player=host.ui) or "Presentation created."


def _spreadsheet_builder(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    if not args.get("worksheets") and not args.get("sheets"):
        from actions.office_generator import generate_spreadsheet_from_prompt

        topic = args.get("topic") or args.get("title") or "Spreadsheet"
        return (
            generate_spreadsheet_from_prompt(topic, player=host.ui, speak=host.speak)
            or "Spreadsheet created."
        )
    from actions.office_builder import create_spreadsheet

    return create_spreadsheet(parameters=args, player=host.ui) or "Spreadsheet created."


def _reminder(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.reminder import reminder

    return (
        reminder(parameters=args, response=None, player=host.ui, session_memory=None)
        or "Reminder set."
    )


def _auto_heal_action(name: str):
    """Build the auto-heal adapter, with the forced action for an alias."""

    def handler(args: Dict[str, Any], context: Any) -> str:
        host = _host_for(context)
        if host is None:
            return desktop_host.MISSING_HOST
        from actions.auto_heal_engine import auto_heal

        parameters = dict(args or {})
        if name == "rollback":
            parameters.setdefault("action", "rollback")
        return auto_heal(parameters=parameters, player=host.ui, speak=host.speak) or (
            "Auto-heal completed."
        )

    return handler


def _shutdown_brahma(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    try:
        host.ui.write_log("SYS: Shutdown requested.")
    except Exception:
        pass
    try:
        host.speak("Goodbye, sir.")
    except Exception:
        pass

    def _exit() -> None:
        import os
        import time

        time.sleep(1)
        os._exit(0)

    threading.Thread(target=_exit, daemon=True, name="brahma-shutdown").start()
    return "Shutting down Brahma."


def _file_controller(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.file_controller import file_controller

    return file_controller(parameters=args, player=host.ui) or "Done."


def _smart_organizer(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.desktop_organizer_mcp import smart_organizer

    return smart_organizer(parameters=args, player=host.ui, speak=host.speak) or "Done."


def _save_memory(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from memory.memory_manager import update_memory

    category = str(args.get("category") or "notes")
    key = str(args.get("key") or "")
    value = str(args.get("value") or "")
    if key and value:
        update_memory({category: {key: {"value": value}}})
        print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
        try:
            host.ui.finish_task_workspace("Memory saved.", "Memory updated.", 100)
        except Exception:
            pass
    return "ok"


def _unlock_device(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.unlock_device import unlock_device

    return unlock_device(parameters=args, player=host.ui) or "Done."


def _connect_action(fn_name: str):
    """Build a thin adapter for a ``brahma_connect`` entry point."""

    def handler(args: Dict[str, Any], context: Any) -> str:
        host = _host_for(context)
        if host is None:
            return desktop_host.MISSING_HOST
        from actions import brahma_connect

        fn = getattr(brahma_connect, fn_name)
        return fn(parameters=args, player=host.ui) or "Done."

    return handler


def _send_message(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.send_message import send_message

    return (
        send_message(
            parameters=args, response=None, player=host.ui, session_memory=None
        )
        or f"Message sent to {args.get('receiver')}."
    )


def _check_instagram_messages(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.instagram_mcp import get_recent_messages

    amount = int(args.get("amount", 5)) if args else 5
    return get_recent_messages(amount) or "Done."


def _instagram_send_dm(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.instagram_mcp import InstagramService

    recipient = args.get("recipient", "")
    message = args.get("message", "")
    open_browser = args.get("open_in_browser", True)
    res = InstagramService.instance().send_dm(
        recipient, message, open_in_browser=open_browser
    )
    return f"Direct message sent to @{recipient}. Chat opened in browser: {res.get('browser_url')}"


def _instagram_post_photo(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.instagram_mcp import InstagramService

    image_path = args.get("image_path", "")
    caption = args.get("caption", "")
    open_browser = args.get("open_in_browser", True)
    res = InstagramService.instance().post_photo(
        image_path, caption=caption, open_in_browser=open_browser
    )
    return f"Successfully published photo to Instagram! Post URL: {res.get('post_url')}"


def _instagram_post_reel(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.instagram_mcp import InstagramService

    video_path = args.get("video_path", "")
    caption = args.get("caption", "")
    thumb = args.get("thumbnail_path")
    open_browser = args.get("open_in_browser", True)
    res = InstagramService.instance().post_reel(
        video_path, caption=caption, thumbnail_path=thumb, open_in_browser=open_browser
    )
    return f"Successfully published Reel to Instagram! Reel URL: {res.get('reel_url')}"


def _instagram_get_user_info(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.instagram_mcp import InstagramService

    username = args.get("username", "")
    res = InstagramService.instance().get_user_profile(username)
    return json.dumps(res, indent=2)


def _instagram_reply(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.instagram_mcp import InstagramService, add_auto_thread

    action = args.get("action")
    reply_text = args.get("reply_text")
    recipient = args.get("recipient") or args.get("username")
    thread_id = None
    if not recipient:
        try:
            inbox = InstagramService.instance().get_inbox(amount=1)
            if inbox:
                recipient = inbox[0].get("sender")
                thread_id = inbox[0].get("thread_id")
        except Exception:
            pass

    if not recipient:
        return (
            "Could not find a recent conversation to reply to. "
            "Please specify who you want to message."
        )
    if action == "take_over":
        add_auto_thread(thread_id or recipient)
        return (
            f"Successfully took over the chat with @{recipient}. "
            "Brahma Evo will now automatically reply."
        )
    InstagramService.instance().send_dm(recipient, reply_text, open_in_browser=True)
    return f"Sent reply to @{recipient}: '{reply_text}'. Thread opened in browser."


def _undo(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from core import undo as undo_stack

    action = args.get("action", "undo")
    if action == "list":
        items = undo_stack.history()
        result = (
            "Things I can undo, most recent first:\n"
            + "\n".join(f"  • {item}" for item in items)
            if items
            else "I have not changed anything I can undo yet."
        )
    else:
        result = undo_stack.undo_last()
    try:
        host.speak("Undone.")
    except Exception:
        pass
    return str(result)


def _recall_memory(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from memory.memory_manager import search_memory

    query = str(args.get("query") or "")
    result = search_memory(query, limit=8)
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


def _execute_protocol(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from core.protocols import protocols

    protocol_name = str(args.get("protocol") or "")
    res = protocols.execute(protocol_name)
    msg = (
        res.get("result", res.get("error", "Protocol completed."))
        if isinstance(res, Mapping)
        else str(res or "Protocol completed.")
    )
    try:
        host.speak(str(msg))
    except Exception:
        pass
    return json.dumps(res, ensure_ascii=False) if isinstance(res, Mapping) else str(msg)


def _get_sensorium_telemetry(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from core.sensorium import sensorium

    snapshot = sensorium.get_snapshot()
    return (
        json.dumps(snapshot, ensure_ascii=False)
        if isinstance(snapshot, Mapping)
        else str(snapshot)
    )


def _manage_local_model(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from core.local_brain import local_brain

    action = args.get("action", "status")
    model_target = args.get("model_name", "")
    if action == "status":
        avail = local_brain.is_available()
        models = local_brain.list_installed_models()
        res: Dict[str, Any] = {
            "server_online": avail,
            "active_model": local_brain.default_model,
            "local_mode_enabled": local_brain.enabled,
            "installed_models": models,
        }
        msg = (
            f"Local brain is {'online' if avail else 'offline'}. "
            f"Installed models: {', '.join(models) if models else 'none'}."
        )
    elif action == "pull" and model_target:
        local_brain.pull_model_async(model_target)
        msg = f"Initiated background download of local model: {model_target}."
        res = {"status": "downloading", "model": model_target}
    elif action == "toggle_offline":
        local_brain.enabled = not local_brain.enabled
        msg = (
            f"Offline local inference mode "
            f"{'engaged' if local_brain.enabled else 'disengaged'}."
        )
        res = {"local_mode_enabled": local_brain.enabled}
    elif action == "set_active" and model_target:
        local_brain.default_model = model_target
        msg = f"Active local model set to {model_target}."
        res = {"active_model": local_brain.default_model}
    else:
        msg = "Invalid action."
        res = {"error": msg}
    try:
        host.speak(msg)
    except Exception:
        pass
    return json.dumps(res, ensure_ascii=False)


def _computer_settings(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.computer_settings import computer_settings

    return computer_settings(parameters=args, player=host.ui) or "Settings updated."


def _dev_agent(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.dev_agent import dev_agent

    return dev_agent(parameters=args, player=host.ui, speak=host.speak) or "Done."


def _open_app(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.open_app import open_app

    return (
        open_app(parameters=args, response=None, player=host.ui)
        or f"Opened {args.get('app_name')}."
    )


def _system_manager(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.system_manager import run as sm_run

    return sm_run(parameters=args, player=host.ui) or "System status retrieved."


def _background_monitor(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.background_monitor import run as bm_run

    return bm_run(parameters=args, player=host.ui) or "Done."


def _clipboard_processor(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.clipboard_processor import process_clipboard

    return process_clipboard(parameters=args, player=host.ui) or "Clipboard read."


def _weather_report(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.weather_report import weather_action

    return weather_action(parameters=args, player=host.ui) or "Weather delivered."


def _browser_control(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.browser_control import browser_control

    return browser_control(parameters=args, player=host.ui) or "Done."


def _youtube_video(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.youtube_video import youtube_video

    return youtube_video(parameters=args, response=None, player=host.ui) or "Done."


def _file_processor(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.file_processor import file_processor

    parameters = dict(args or {})
    if not parameters.get("file_path") and host.ui.current_file:
        parameters["file_path"] = host.ui.current_file
    return (
        file_processor(parameters=parameters, player=host.ui, speak=host.speak)
        or "Done."
    )


def _screen_process(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.screen_processor import screen_process

    if hasattr(host.ui, "set_scanning"):
        try:
            host.ui.set_scanning(True, "SCANNING SCREEN")
        except Exception:
            pass
    threading.Thread(
        target=screen_process,
        kwargs={
            "parameters": args,
            "response": None,
            "player": host.ui,
            "session_memory": None,
        },
        daemon=True,
    ).start()
    return "Vision module activated. Stay completely silent — vision module will speak directly."


def _smart_home_control(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    command_text = str(args.get("command") or "").strip()
    live = getattr(host, "live", None)
    smart_home = getattr(host, "smart_home", None) or getattr(live, "_smart_home", None)
    if smart_home is None:
        from core.smart_home import SmartHomeService

        smart_home = SmartHomeService()
    r = smart_home.execute_command(command_text)
    return str((r or {}).get("detail") or "Smart-home command completed.")


def _desktop_control(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.desktop import desktop_control

    return desktop_control(parameters=args, player=host.ui) or "Done."


def _agent_task(args: Dict[str, Any], context: Any) -> str:
    return (
        "agent_task is retired. Use the agent harness tools "
        "(run_command, read_file, write_file, run_subagent) instead."
    )


def _web_search(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.web_search import web_search as web_search_action

    return web_search_action(parameters=args, player=host.ui) or "Done."


def _computer_control(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.computer_control import computer_control

    return computer_control(parameters=args, player=host.ui) or "Done."


def _game_updater(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.game_updater import game_updater

    return game_updater(parameters=args, player=host.ui, speak=host.speak) or "Done."


def _flight_finder(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.flight_finder import flight_finder

    return flight_finder(parameters=args, player=host.ui) or "Done."


def _circuit_assembler(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.circuit_assembler import circuit_assembler

    r = circuit_assembler(parameters=args, player=host.ui, speak=host.speak)
    if isinstance(r, dict):
        return str(r.get("summary") or "Circuit schematic ready.")
    return str(r or "Circuit schematic ready.")


def _geospatial_globe(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from core.globe_window import GlobeWindow

    globe = GlobeWindow.get_instance(parent=getattr(host.ui, "_win", None))
    action = (args.get("action") or "open").lower().strip()
    location = args.get("location") or "current"
    if action == "open":
        globe.open_globe(location if args.get("location") else None)
        return "Opened the interactive Brahma map."
    if action == "route":
        r = globe.show_route(args.get("origin", ""), args.get("destination", ""))
        return (
            f"Flight route: {r.get('origin')} to {r.get('destination')}, "
            f"{r.get('distance_km')} km, about {r.get('flight_time')}."
        )
    if action == "drive":
        r = globe.show_driving_route(
            args.get("origin", ""), args.get("destination", "")
        )
        return (
            f"Driving route: {r.get('origin')} to {r.get('destination')}, "
            f"{r.get('distance_km')} km, {r.get('duration_str')}."
        )
    if action in ("location", "fly_to"):
        r = (
            globe.show_location(location)
            if action == "location"
            else globe.fly_to(location)
        )
        return f"Map focused on {r.get('location', location)}."
    if action == "weather":
        r = globe.show_weather(location)
        return f"Weather for {r.get('location')}: {r.get('weather')}."
    if action == "flights":
        r = globe.show_live_flights(args.get("location"))
        return f"Showing {len(r)} live aircraft on the map."
    if action == "iss":
        r = globe.show_iss_tracker()
        return (
            f"ISS location: {r.get('lat')}, {r.get('lon')}; "
            f"altitude {r.get('altitude_km')} km."
        )
    if action == "earthquakes":
        r = globe.show_earthquakes(float(args.get("min_magnitude", 2.5)))
        return f"Showing {len(r)} recent earthquakes on the map."
    if action == "nearby":
        r = globe.show_nearby(args.get("query") or "hospitals", args.get("location"))
        return f"Found {len(r)} nearby places."
    if action == "radar":
        enabled = bool(args.get("enable", True))
        globe.toggle_weather_radar(enabled)
        return f"Weather radar {'enabled' if enabled else 'disabled'}."
    return f"Unsupported map action: {action}."


def _call_screening(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.call_assistant import (
        hang_up_active_call,
        start_call_proxy,
        take_over_active_call,
    )

    action = (args.get("action") or "start").lower().strip()
    if action == "take_over":
        take_over_active_call()
        return "Taking over the screened call."
    if action == "hang_up":
        hang_up_active_call()
        return "Ended the screened call."
    if action == "start":
        event = {
            "title": args.get("caller") or "Incoming call",
            "app": args.get("app") or "Phone / Call",
        }
        from core.confirm import request

        return str(
            request(
                "start-call-screening",
                "Answer this call as Brahma Evo",
                f"Brahma will answer {event['title']} in {event['app']}, "
                "listen to the caller, and prepare a transcript and summary.",
                lambda: (
                    start_call_proxy(event, ui=host.ui, speak_fn=host.speak)
                    and "Call screening started."
                ),
            )
        )
    return "Choose start, take_over, or hang_up."


def _skill_forge(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from core.dynamic_registry import DynamicToolRegistry

    action = (args.get("action") or "forge").lower().strip()
    if action == "list":
        skills = DynamicToolRegistry.list_skills()
        return "Installed skills: " + (
            ", ".join(skill["name"] for skill in skills) or "none"
        )
    if action == "forge":
        goal = (args.get("goal") or "").strip()
        if not goal:
            return "Describe the capability you want Brahma to learn."
        live = getattr(host, "live", None)
        forge_fn = getattr(live, "_forge_skill", None)
        if callable(forge_fn):
            threading.Thread(
                target=forge_fn,
                args=(goal, args.get("skill_name")),
                daemon=True,
                name="skill-forge",
            ).start()
        return f"Started creating the requested skill: {goal}"
    return "Choose forge or list."


def _dynamic_skill(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from core.dynamic_registry import DynamicToolRegistry

    action = (args.get("action") or "list").lower().strip()
    if action == "list":
        skills = DynamicToolRegistry.list_skills()
        return "Installed skills: " + (
            ", ".join(skill["name"] for skill in skills) or "none"
        )
    if action == "run":
        skill_name = (args.get("skill_name") or "").strip()
        if not DynamicToolRegistry.has_tool(skill_name):
            return f"No active skill named '{skill_name}'."
        run_res = DynamicToolRegistry.execute_sync(
            skill_name, args.get("arguments") or args or {}
        )
        if isinstance(run_res, dict):
            out_text = str(
                run_res.get("summary")
                or run_res.get("output")
                or run_res.get("text")
                or run_res
            ).strip()
        else:
            out_text = str(run_res).strip()
        try:
            host.ui.write_log(f"Brahma Evo [{skill_name}]:\n{out_text}")
        except Exception:
            pass
        return out_text
    return "Choose list or run."


def _spotify_controller(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.spotify_controller import spotify_controller

    return (
        spotify_controller(parameters=args, player=host.ui, speak=host.speak)
        or "Done."
    )


def _calendar_scheduler(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.calendar_scheduler import calendar_scheduler

    return (
        calendar_scheduler(parameters=args, player=host.ui, speak=host.speak)
        or "Done."
    )


def _google_workspace(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.google_workspace_mcp import google_workspace

    return (
        google_workspace(parameters=dict(args or {}), player=host.ui, speak=host.speak)
        or "Google Workspace task completed."
    )


def _system_diagnostics_action(name: str):
    """Build the system-diagnostics adapter with forced action for aliases."""

    def handler(args: Dict[str, Any], context: Any) -> str:
        host = _host_for(context)
        if host is None:
            return desktop_host.MISSING_HOST
        from actions.system_diagnostics_mcp import system_diagnostics

        p = dict(args or {})
        if name == "ram_hogs":
            p.setdefault("action", "ram_hogs")
        elif name == "kill_process":
            p.setdefault("action", "kill")
        elif name == "brightness_control":
            p.setdefault("action", "brightness")
        return (
            system_diagnostics(parameters=p, player=host.ui, speak=host.speak)
            or "Diagnostics completed."
        )

    return handler


def _daily_briefing(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.daily_briefing import daily_briefing

    return (
        daily_briefing(parameters=args, player=host.ui, speak=host.speak)
        or "Delivered daily briefing."
    )


def _calorie_counter(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.calorie_counter import calorie_counter

    return (
        calorie_counter(parameters=args, player=host.ui, speak=host.speak)
        or "Nutrition analysis complete."
    )


def _pushup_counter(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.pushup_counter import pushup_counter

    return (
        pushup_counter(parameters=args, player=host.ui, speak=host.speak)
        or "Workout session complete."
    )


def _upload_video(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.upload_video import upload_video

    return (
        upload_video(parameters=args, player=host.ui, speak=host.speak)
        or "Video publishing ready."
    )




def _generate_image(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.image_generator import generate_image

    return (
        generate_image(parameters=args, player=host.ui, speak=host.speak)
        or "Image generated."
    )


def _reverse_engineer(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.reverse_engineer import reverse_engineer

    return (
        reverse_engineer(parameters=args, player=host.ui, speak=host.speak)
        or "Reverse engineering complete."
    )


def _game_sentinel(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.game_sentinel import game_sentinel

    return (
        game_sentinel(parameters=args, player=host.ui)
        or "Game Sentinel executed."
    )


def _study_guard(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.study_guard import study_guard

    return (
        study_guard(parameters=args, player=host.ui)
        or "Study Guard executed."
    )


def _net_sentinel(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from actions.net_sentinel import net_sentinel

    return (
        net_sentinel(parameters=args, player=host.ui)
        or "Net Sentinel executed."
    )


def _space_cleaner(args: Dict[str, Any], context: Any) -> str:
    host = _host_for(context)
    if host is None:
        return desktop_host.MISSING_HOST
    from core.space_cleaner import get_space_cleaner

    action = str(args.get("action", "scan")).lower()
    cleaner = get_space_cleaner()
    if action in ("purge", "clean"):
        cats = args.get("categories") or ["system_temp", "browser_cache"]
        res = cleaner.purge_selected(cats)
        return res.get("message") or f"Purged {res.get('freed_mb', 0)} MB."
    else:
        res = cleaner.scan_all()
        return f"Scan complete. Found {res.total_reclaimable_mb} MB reclaimable across {len(res.categories)} categories."

HANDLERS = {
    "pdf_document": _pdf_document,
    "word_document": _word_document,
    "presentation_builder": _presentation_builder,
    "spreadsheet_builder": _spreadsheet_builder,
    "reminder": _reminder,
    "auto_heal": _auto_heal_action("auto_heal"),
    "self_patch": _auto_heal_action("self_patch"),
    "rollback": _auto_heal_action("rollback"),
    "shutdown_brahma": _shutdown_brahma,
    "file_controller": _file_controller,
    "smart_organizer": _smart_organizer,
    "desktop_organizer": _smart_organizer,
    "save_memory": _save_memory,
    "unlock_device": _unlock_device,
    "connect_list_devices": _connect_action("connect_list_devices"),
    "connect_get_device": _connect_action("connect_get_device"),
    "connect_get_capabilities": _connect_action("connect_get_capabilities"),
    "connect_execute": _connect_action("connect_execute"),
    "connect_pair_device": _connect_action("connect_pair_device"),
    "connect_disconnect_device": _connect_action("connect_disconnect_device"),
    "send_message": _send_message,
    "check_instagram_messages": _check_instagram_messages,
    "instagram_send_dm": _instagram_send_dm,
    "instagram_post_photo": _instagram_post_photo,
    "instagram_post_reel": _instagram_post_reel,
    "instagram_get_user_info": _instagram_get_user_info,
    "instagram_reply": _instagram_reply,
    "undo": _undo,
    "recall_memory": _recall_memory,
    "execute_protocol": _execute_protocol,
    "get_sensorium_telemetry": _get_sensorium_telemetry,
    "manage_local_model": _manage_local_model,
    "computer_settings": _computer_settings,
    "dev_agent": _dev_agent,
    "open_app": _open_app,
    "system_manager": _system_manager,
    "background_monitor": _background_monitor,
    "clipboard_processor": _clipboard_processor,
    "weather_report": _weather_report,
    "browser_control": _browser_control,
    "youtube_video": _youtube_video,
    "file_processor": _file_processor,
    "screen_process": _screen_process,
    "smart_home_control": _smart_home_control,
    "desktop_control": _desktop_control,
    "agent_task": _agent_task,
    "web_search": _web_search,
    "computer_control": _computer_control,
    "game_updater": _game_updater,
    "flight_finder": _flight_finder,
    "circuit_assembler": _circuit_assembler,
    "geospatial_globe": _geospatial_globe,
    "call_screening": _call_screening,
    "skill_forge": _skill_forge,
    "dynamic_skill": _dynamic_skill,
    "spotify_controller": _spotify_controller,
    "spotify": _spotify_controller,
    "music": _spotify_controller,
    "calendar_scheduler": _calendar_scheduler,
    "calendar": _calendar_scheduler,
    "schedule": _calendar_scheduler,
    "daily_briefing": _daily_briefing,
    "briefing": _daily_briefing,
    "google_workspace": _google_workspace,
    "gmail": _google_workspace,
    "google_calendar": _google_workspace,
    "google_drive": _google_workspace,
    "workspace": _google_workspace,
    "system_diagnostics": _system_diagnostics_action("system_diagnostics"),
    "diagnostics": _system_diagnostics_action("diagnostics"),
    "os_hardware": _system_diagnostics_action("os_hardware"),
    "hardware_control": _system_diagnostics_action("hardware_control"),
    "ram_hogs": _system_diagnostics_action("ram_hogs"),
    "kill_process": _system_diagnostics_action("kill_process"),
    "brightness_control": _system_diagnostics_action("brightness_control"),
    "calorie_counter": _calorie_counter,
    "nutrition_scan": _calorie_counter,
    "food_analysis": _calorie_counter,
    "pushup_counter": _pushup_counter,
    "workout_tracker": _pushup_counter,
    "rep_counter": _pushup_counter,
    "upload_video": _upload_video,
    "video_publisher": _upload_video,
    "publish_video": _upload_video,
    "generate_image": _generate_image,
    "create_image": _generate_image,
    "text_to_image": _generate_image,
    "draw_image": _generate_image,
    "ai_art": _generate_image,
    "image_gen": _generate_image,
    "reverse_engineer": _reverse_engineer,
    "game_sentinel": _game_sentinel,
    "study_guard": _study_guard,
    "net_sentinel": _net_sentinel,
    "space_cleaner": _space_cleaner,
}


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #



FALLBACK_DECLARATIONS: Dict[str, Dict[str, Any]] = {}


def _get_fallback_declaration(name: str) -> Optional[Dict[str, Any]]:
    if not FALLBACK_DECLARATIONS:
        for mod_name, plugin_attr in (
            ("actions.image_generator", "PLUGIN"),
            ("actions.reverse_engineer", "PLUGIN"),
            ("actions.game_sentinel", "PLUGIN"),
            ("actions.study_guard", "PLUGIN"),
            ("actions.net_sentinel", "PLUGIN"),
        ):
            try:
                mod = __import__(mod_name, fromlist=[plugin_attr])
                p = getattr(mod, plugin_attr, None)
                if p and isinstance(p, dict) and "name" in p:
                    FALLBACK_DECLARATIONS[p["name"]] = p
            except Exception:
                pass
        FALLBACK_DECLARATIONS["space_cleaner"] = {
            "name": "space_cleaner",
            "description": "Scans and cleans disk storage, purging temporary files and cache.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "action": {"type": "STRING", "description": "scan | purge"},
                    "categories": {"type": "ARRAY", "description": "Categories to purge"},
                },
            },
        }
    return FALLBACK_DECLARATIONS.get(name)

def build_specs(
    declarations: Iterable[Mapping[str, Any]] = (),
) -> List[ToolSpec]:
    """The wave 1-3 specs. ``declarations`` is the host's tool catalogue.

    The schema is taken from ``main.TOOL_DECLARATIONS`` rather than restated
    here: one source for what the model is told, so the voice surface and the
    agent surface cannot drift. A name without a declaration (``self_patch``,
    ``rollback``, ``desktop_organizer``, ...) borrows its alias's schema; when
    nothing is supplied -- a unit test that only cares about dispatch -- it
    gets a permissive empty object.
    """
    declared = _declarations(declarations)
    specs: List[ToolSpec] = []
    waves = (("wave1", WAVE1), ("wave2", WAVE2), ("wave3", WAVE3))
    for wave_tag, table in waves:
        for name, (tool_class, needs_approval) in table.items():
            source = (
                declared.get(name)
                or declared.get(ALIASES.get(name, ""))
                or _get_fallback_declaration(name)
                or _get_fallback_declaration(ALIASES.get(name, ""))
            )
            parameters = lowercase_schema(
                (source or {}).get("parameters") or _empty_schema()
            )
            description = str(
                (source or {}).get("description") or f"Desktop action {name}."
            )
            alias_of = ALIASES.get(name)
            if alias_of and not declared.get(name):
                if alias_of == "auto_heal":
                    verb = "forces" if name == "rollback" else "defaults"
                    description = f"{description} ({name} {verb} the auto-heal action.)"
                else:
                    description = f"{description} ({name} is an alias for {alias_of}.)"
            specs.append(
                ToolSpec(
                    name=name,
                    description=description,
                    parameters=parameters,
                    tool_class=tool_class,
                    handler=HANDLERS[name],
                    tags=(DESKTOP_TAG, wave_tag),
                    requires_approval=needs_approval,
                    classifier=CLASSIFIERS.get(name),
                    silent=name in SILENT_TOOLS,
                )
            )
    return specs


def register(declarations: Iterable[Mapping[str, Any]] = ()) -> List[str]:
    """Publish desktop waves in the process-wide registry. Idempotent."""
    specs = build_specs(declarations)
    for spec in specs:
        register_spec(spec, replace=True)
    return [spec.name for spec in specs]


def desktop_names() -> frozenset:
    """Every registered desktop tool, by tag (no second list to maintain)."""
    return frozenset(
        spec.name
        for spec in (get_spec(name) for name in (*WAVE1, *WAVE2, *WAVE3))
        if spec is not None and DESKTOP_TAG in spec.tags
    )


def attach_host(live: Any) -> desktop_host.HostEventSink:
    """Bind this app instance's HUD feedback and host bundle to the runtime.

    Called when the GUI has a live instance: from ``_finish_tool_turn`` for the
    text door, and from the tool executor for the voice door. Both doors share
    one runtime, so this is idempotent -- rebuilding for the same live instance
    keeps any sink it was already chaining instead of dropping it.

    ``default_extra`` is the second half: ``AgentLoop`` builds the per-call
    ``extra`` itself (it adds the subagent runner), so a text turn cannot pass
    ``{"host": ...}`` the way ``core/tool_exec.py`` does. The runtime merges its
    defaults underneath that mapping, which keeps the injection point the same
    for both doors without teaching ``agent/`` what a host is.
    """
    from agent import runtime as agent_runtime

    runtime = agent_runtime.current()
    previous = runtime.event_sink
    chained = previous.also if isinstance(previous, desktop_host.HostEventSink) else previous
    sink = desktop_host.HostEventSink(
        host=desktop_host.LiveHost(live),
        names=desktop_names(),
        also=chained,
    )
    runtime.event_sink = sink
    runtime.default_extra = {**runtime.default_extra, "host": sink.host}
    return sink


__all__ = [
    "CALENDAR_READ_ACTIONS",
    "CLASSIFIERS",
    "DESKTOP_TAG",
    "DIAGNOSTICS_BRIGHTNESS_ACTIONS",
    "DIAGNOSTICS_READ_ACTIONS",
    "FILE_CONTROLLER_READ_ACTIONS",
    "HANDLERS",
    "SILENT_TOOLS",
    "SMART_ORGANIZER_READ_ACTIONS",
    "WAVE1",
    "WAVE2",
    "WAVE3",
    "attach_host",
    "build_specs",
    "desktop_names",
    "register",
]
