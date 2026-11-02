"""Desktop actions registered as ``ToolSpec``s (ADR-019, wave 1).

The voice and text doors used to dispatch every native capability through the
``elif`` chain in :mod:`core.tool_exec`, which is the one execution path with no
permission check. This module publishes the first wave of native actions as
ordinary specs instead, so a call goes through
:meth:`agent.runtime.AgentRuntime.execute` -- policy, approval, then handler --
like every other tool.

Two rules keep the wave honest:

* **Registration is host-side.** ``agent/`` never imports this module, never
  learns an action name, and stays importable with only pytest installed.
* **Adapters, not rewrites.** Each handler is a thin wrapper over the existing
  ``actions/`` function, with the ``player``/``speak`` keyword arguments it
  already accepts. The 22 module signatures do not change.

Wave 1 is the unambiguous slice: the four document producers (``write``) plus
the process-spawning/self-mutating actions (``exec``). ``file_controller`` and
``smart_organizer`` mix read and write actions, and ``send_message``/Instagram
are network tools; those need a per-action class decision and belong to wave 2.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

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

#: Names the model may use that are not their own declaration. ``rollback``
#: also forces its action; ``self_patch`` is the plain auto-heal entry point.
ALIASES: Dict[str, str] = {
    "self_patch": "auto_heal",
    "rollback": "auto_heal",
}

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
}


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def build_specs(
    declarations: Iterable[Mapping[str, Any]] = (),
) -> List[ToolSpec]:
    """The wave-1 specs. ``declarations`` is the host's tool catalogue.

    The schema is taken from ``main.TOOL_DECLARATIONS`` rather than restated
    here: one source for what the model is told, so the voice surface and the
    agent surface cannot drift. A name without a declaration (``self_patch``,
    ``rollback``) borrows its alias's schema; when nothing is supplied -- a unit
    test that only cares about dispatch -- it gets a permissive empty object.
    """
    declared = _declarations(declarations)
    specs: List[ToolSpec] = []
    for name, (tool_class, needs_approval) in WAVE1.items():
        source = declared.get(name) or declared.get(ALIASES.get(name, ""))
        parameters = lowercase_schema(
            (source or {}).get("parameters") or _empty_schema()
        )
        description = str((source or {}).get("description") or f"Desktop action {name}.")
        alias_of = ALIASES.get(name)
        if alias_of and not declared.get(name):
            verb = "forces" if name == "rollback" else "defaults"
            description = f"{description} ({name} {verb} the auto-heal action.)"
        specs.append(
            ToolSpec(
                name=name,
                description=description,
                parameters=parameters,
                tool_class=tool_class,
                handler=HANDLERS[name],
                tags=(DESKTOP_TAG, "wave1"),
                requires_approval=needs_approval,
            )
        )
    return specs


def register(declarations: Iterable[Mapping[str, Any]] = ()) -> List[str]:
    """Publish wave 1 in the process-wide registry. Idempotent."""
    specs = build_specs(declarations)
    for spec in specs:
        register_spec(spec, replace=True)
    return [spec.name for spec in specs]


def desktop_names() -> frozenset:
    """Every registered desktop tool, by tag (no second list to maintain)."""
    return frozenset(
        spec.name
        for spec in (get_spec(name) for name in WAVE1)
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
    "DESKTOP_TAG",
    "HANDLERS",
    "WAVE1",
    "attach_host",
    "build_specs",
    "desktop_names",
    "register",
]
