"""Desktop actions registered as ``ToolSpec``s (ADR-019, waves 1-2).

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
(``send_message``, Instagram), and ``save_memory`` (``silent=True``).
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

#: Names the model may use that are not their own declaration. ``rollback``
#: also forces its action; ``self_patch`` is the plain auto-heal entry point;
#: ``desktop_organizer`` is the legacy alias for ``smart_organizer``.
ALIASES: Dict[str, str] = {
    "self_patch": "auto_heal",
    "rollback": "auto_heal",
    "desktop_organizer": "smart_organizer",
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


CLASSIFIERS: Dict[str, Callable[[Mapping[str, Any]], str]] = {
    "file_controller": _classify_file_controller,
    "smart_organizer": _classify_smart_organizer,
    "desktop_organizer": _classify_smart_organizer,
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
}


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def build_specs(
    declarations: Iterable[Mapping[str, Any]] = (),
) -> List[ToolSpec]:
    """The wave-1 and wave-2 specs. ``declarations`` is the host's tool catalogue.

    The schema is taken from ``main.TOOL_DECLARATIONS`` rather than restated
    here: one source for what the model is told, so the voice surface and the
    agent surface cannot drift. A name without a declaration (``self_patch``,
    ``rollback``, ``desktop_organizer``) borrows its alias's schema; when
    nothing is supplied -- a unit test that only cares about dispatch -- it
    gets a permissive empty object.
    """
    declared = _declarations(declarations)
    specs: List[ToolSpec] = []
    waves = (("wave1", WAVE1), ("wave2", WAVE2))
    for wave_tag, table in waves:
        for name, (tool_class, needs_approval) in table.items():
            source = declared.get(name) or declared.get(ALIASES.get(name, ""))
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
        for spec in (get_spec(name) for name in (*WAVE1, *WAVE2))
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
    "CLASSIFIERS",
    "DESKTOP_TAG",
    "FILE_CONTROLLER_READ_ACTIONS",
    "HANDLERS",
    "SILENT_TOOLS",
    "SMART_ORGANIZER_READ_ACTIONS",
    "WAVE1",
    "WAVE2",
    "attach_host",
    "build_specs",
    "desktop_names",
    "register",
]
