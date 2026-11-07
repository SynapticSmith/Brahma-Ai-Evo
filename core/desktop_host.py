"""Host services for native desktop actions (ADR-019).

A migrated action is an ordinary :class:`~agent.spec.ToolSpec` handler with the
portable ``(args, context) -> str`` shape. Everything it needs from the running
app -- the window facade, the voice, the GUI event loop -- arrives as one bundle
at ``context.extra["host"]``. Nothing in ``agent/`` learns about PyQt, and an
action reaches for no global.

The bundle is deliberately small (:class:`HostServices`: ``ui``, ``speak``,
``submit``). A handler that runs without one (the CLI, or a test that forgot the
fixture) gets :data:`MISSING_HOST` as text instead of a silent no-op or a
global fallback: "the tool surface differs per door" stays observable.

The HUD presentation lives here too. ``AgentRuntime`` already knows when a tool
starts and ends, so :class:`HostEventSink` subscribes and calls
:func:`present_start` / :func:`present_end`. An action never calls the UI, and
the same action behaves the same in the GUI, the CLI and a test. The legacy
``elif`` chain in :mod:`core.tool_exec` calls the same two functions while it is
being retired, so "what the user sees" stays one implementation.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, runtime_checkable

#: Text a handler returns when it was invoked outside the desktop app. It is a
#: diagnostic, not a fallback: the model reads it and stops retrying.
MISSING_HOST = (
    "This action needs the Brahma Evo desktop host (its window, voice and task "
    "panel). It is only available from the GUI."
)

#: A file the result mentions, used by the deliverables panel. Deliberately
#: conservative: a Windows absolute path with one of the office/media suffixes.
_FILE_RE = re.compile(
    r'([A-Za-z]:\\[^\s"\'<>`\r\n]+\.(?:pdf|docx|xlsx|pptx|png|jpg|mp4|py|html|json|txt))'
)


@runtime_checkable
class HostUI(Protocol):
    """The part of ``BrahmaUI`` an action may touch.

    ``write_log``/``set_scanning``/``show_content`` are not here: logging is the
    host's business, and a handler that writes to the log is a handler that
    reaches around its caller.
    """

    muted: bool
    current_file: Optional[str]

    def set_state(self, state: str) -> None: ...

    def show_hud_operation(
        self,
        title: str,
        step: str,
        sources: Optional[List[str]] = None,
        tool: Optional[str] = None,
        **kwargs: Any,
    ) -> None: ...

    def show_hud_deliverable(
        self,
        title: str,
        summary: str = "",
        bullets: Optional[List[str]] = None,
        file_path: Optional[str] = None,
        kind: Optional[str] = None,
        **kwargs: Any,
    ) -> None: ...

    def update_task_workspace(self, **kwargs: Any) -> None: ...

    def finish_task_workspace(
        self, result: str, status: str = "Task completed.", percent: int = 100
    ) -> None: ...


@runtime_checkable
class HostServices(Protocol):
    """What an action may ask of the application it is running inside."""

    ui: HostUI

    def speak(self, text: str) -> None: ...

    def submit(self, coro: Any) -> Any:
        """Run ``coro`` on the app's event loop (for the few async actions)."""


def require_host(context: Any) -> Optional[HostServices]:
    """The injected host, or ``None``. Never a module global."""
    extra = getattr(context, "extra", None) or {}
    host = extra.get("host") if isinstance(extra, Mapping) else None
    return host if host is not None else None


# --------------------------------------------------------------------------- #
# Presentation: one implementation for the GUI, the legacy chain and a test
# --------------------------------------------------------------------------- #


def _sources(name: str, args: Mapping[str, Any]) -> List[str]:
    sources: List[str] = []
    if args.get("url"):
        sources.append(str(args["url"]))
    for key in ("file_path", "path"):
        if args.get(key):
            sources.append(Path(str(args[key])).name)
            break
    if not sources and args.get("query") and name in (
        "web_search",
        "flight_finder",
        "youtube_video",
    ):
        sources.append(f"Query: {str(args['query'])[:28]}")
    return sources


def present_start(host: HostServices, name: str, args: Mapping[str, Any]) -> None:
    """Announce that a desktop action is running (the HUD status moment).

    Status is shown, never spoken. The "Working on ..." banner used to call
    ``host.speak`` here, which is wrong on both voice paths: with Gemini Live it
    injected a ``send_client_content(turn_complete=True)`` *before*
    ``send_tool_response``, starting a second model turn that repeated the
    answer; on the local loop it raced the real reply as a separate TTS
    utterance. The tool result already reaches the model, which owns the spoken
    answer (ADR-019 consequence 4).
    """
    print(f"[BRAHMA EVO] 🔧 {name}  {dict(args or {})}")
    try:
        host.ui.set_state("THINKING")
    except Exception:
        pass

    title = name.replace("_", " ").title()
    brief = (
        args.get("query")
        or args.get("description")
        or args.get("topic")
        or args.get("prompt")
        or args.get("title")
        or args.get("action")
        or ""
    )
    try:
        host.ui.show_hud_operation(
            title=f"{title.upper()} ACTIVE",
            step=f"Processing: {str(brief)[:75]}" if brief else f"Executing {title}...",
            sources=_sources(name, args),
            tool=name,
        )
    except Exception:
        pass
    try:
        host.ui.update_task_workspace(
            title=f"Running {name}",
            status=f"Executing {name}",
            output="Waiting for the tool to finish.",
            percent=45,
        )
    except Exception:
        pass


def _deliverable(name: str, args: Mapping[str, Any], result: Any) -> Dict[str, Any]:
    text = str(result)
    match = _FILE_RE.search(text)
    detected = match.group(1).strip() if match else None
    if not detected and args.get("file_path"):
        candidate = Path(str(args["file_path"]))
        if candidate.exists():
            detected = str(candidate.resolve())

    bullets: List[str] = []
    for line in text.splitlines():
        clean = line.strip()
        if (
            clean.startswith(("-", "*", "•", ">")) or clean.startswith(tuple("123456789."))
        ) and len(clean) > 3:
            bullets.append(clean.lstrip("-*•>0123456789. ").strip())
        elif len(clean) > 15 and not clean.startswith("#") and len(bullets) < 4:
            bullets.append(clean)
    return {
        "title": f"{name.replace('_', ' ').title().upper()} DELIVERABLE",
        "summary": text[:160] if not bullets else "",
        "bullets": bullets[:5],
        "file_path": detected,
        "kind": name,
    }


def connect_tool_voice(name: str, raw_result: Any) -> Optional[str]:
    """One-line spoken summary for a ``connect_*`` action result, or ``None``.

    Lives with the rest of the desktop presentation so :class:`HostServices`
    stays narrow (``ui``, ``speak``, ``submit``) while the migrated Connect
    specs keep their voice line on both doors and in recording-host tests.
    """
    if not str(name or "").startswith("connect_"):
        return None
    try:
        data = (
            json.loads(raw_result)
            if isinstance(raw_result, str)
            else dict(raw_result or {})
        )
    except Exception:
        data = {}

    if name == "connect_list_devices":
        count = int(data.get("count") or len(data.get("devices") or []))
        return f"I found {count} connected device{'s' if count != 1 else ''}."

    if name == "connect_get_device":
        device = data.get("device") or {}
        label = str(device.get("name") or "the device")
        status = "online" if device.get("online") else "offline"
        return f"{label} is {status}."

    if name == "connect_get_capabilities":
        device = data.get("device") or {}
        label = str(device.get("name") or "The device")
        return f"{label} capabilities are ready."

    if name == "connect_pair_device":
        pairing = data.get("pairing") or data
        code = str(pairing.get("pairing_code") or "").strip()
        if code:
            return f"Pairing code ready: {code}."
        return "Pairing is ready."

    if name == "connect_disconnect_device":
        return "The device has been disconnected."

    if name == "connect_execute":
        if data.get("success", False):
            detail = data.get("detail")
            if isinstance(detail, dict):
                detail = (
                    detail.get("message")
                    or detail.get("status")
                    or detail.get("result")
                )
            if not detail and isinstance(data.get("data"), dict):
                payload = data.get("data") or {}
                detail = (
                    payload.get("message")
                    or payload.get("status")
                    or payload.get("result")
                )
            if not detail:
                detail = data.get("error") or "Task completed."
            return str(detail)
        return str(data.get("error") or "The device command failed.")

    return None


def present_end(
    host: HostServices,
    name: str,
    args: Mapping[str, Any],
    result: Any,
    *,
    voice: Optional[str] = None,
) -> None:
    """Render the result, then hand the microphone back.

    ``voice`` is an optional one-line summary (computed automatically for
    ``connect_*`` tools via :func:`connect_tool_voice`). Nothing else here
    speaks: the legacy "``<tool> completed``" chatter is gone, because the
    result reaches the model, which speaks when the result is worth speaking.
    See ADR-019 consequence 4.
    """
    if voice is None:
        voice = connect_tool_voice(name, result)
    try:
        host.ui.finish_task_workspace(str(result), "Task completed.", 100)
    except Exception:
        pass

    try:
        host.ui.show_hud_deliverable(**_deliverable(name, args, result))
    except Exception:
        pass

    if voice:
        try:
            host.ui.write_log(f"Brahma Evo: {voice}")
        except Exception:
            pass
        try:
            host.speak(voice)
        except Exception:
            pass

    try:
        if not host.ui.muted:
            host.ui.set_state("LISTENING")
    except Exception:
        pass

    print(f"[BRAHMA EVO] 📤 {name} → {str(result)[:80]}")


@dataclass
class HostEventSink:
    """Translate runtime ``tool_start``/``tool_end`` events into HUD feedback.

    Only the names in ``names`` are rendered: the runtime emits those events for
    every tool, and a 20-step code-agent turn must not repaint the desktop
    panel. ``also`` chains a previous sink (the CLI printer, or a future
    telemetry sink) instead of silently replacing it.
    """

    host: HostServices
    names: frozenset
    also: Optional[Any] = None

    def __call__(self, event: Mapping[str, Any]) -> None:
        if self.also is not None:
            try:
                self.also(event)
            except Exception:
                pass
        kind = event.get("type")
        if kind == "tool_start":
            self.on_tool_start(event)
        elif kind == "tool_end":
            self.on_tool_end(event)

    def on_tool_start(self, event: Mapping[str, Any]) -> None:
        name = str(event.get("name") or "")
        if name in self.names and not event.get("silent"):
            present_start(self.host, name, event.get("args") or {})

    def on_tool_end(self, event: Mapping[str, Any]) -> None:
        name = str(event.get("name") or "")
        if name not in self.names:
            return
        if event.get("silent"):
            try:
                if not self.host.ui.muted:
                    self.host.ui.set_state("LISTENING")
            except Exception:
                pass
            return
        present_end(
            self.host,
            name,
            event.get("args") or {},
            event.get("output") or "",
        )


# --------------------------------------------------------------------------- #
# Concrete hosts
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LiveHost:
    """Adapt a ``BrahmaLive`` instance to :class:`HostServices`.

    The adapter exists so the protocol stays narrow: ``BrahmaLive`` owns audio,
    sessions and the plugin surface, and an action must not be able to reach any
    of that. ``submit`` is the one bridge to the live event loop, for actions
    that already return a coroutine.
    """

    live: Any

    @property
    def ui(self) -> Any:
        return self.live.ui

    def speak(self, text: str) -> None:
        return self.live.speak(text)

    def submit(self, coro: Any) -> Any:
        loop = getattr(self.live, "_loop", None)
        if loop is not None and loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, loop)
        return asyncio.run(coro)


@dataclass
class RecordingUI:
    """A ``HostUI`` that records instead of painting (tests, headless probes)."""

    muted: bool = False
    current_file: Optional[str] = None
    states: List[str] = field(default_factory=list)
    logs: List[str] = field(default_factory=list)
    operations: List[Dict[str, Any]] = field(default_factory=list)
    deliverables: List[Dict[str, Any]] = field(default_factory=list)
    tasks: List[Dict[str, Any]] = field(default_factory=list)

    def set_state(self, state: str) -> None:
        self.states.append(state)

    def write_log(self, text: str) -> None:
        self.logs.append(text)

    def set_scanning(self, enabled: bool, text: str = "") -> None:
        self.logs.append(f"scanning={bool(enabled)}:{text}")

    def show_hud_operation(self, title: str, step: str, sources=None, tool=None, **kwargs) -> None:
        self.operations.append(
            {"title": title, "step": step, "sources": list(sources or []), "tool": tool, **kwargs}
        )

    def show_hud_deliverable(
        self, title: str, summary: str = "", bullets=None, file_path=None, kind=None, **kwargs
    ) -> None:
        self.deliverables.append(
            {
                "title": title,
                "summary": summary,
                "bullets": list(bullets or []),
                "file_path": file_path,
                "kind": kind,
                **kwargs,
            }
        )

    def update_task_workspace(self, **kwargs) -> None:
        self.tasks.append(dict(kwargs))

    def finish_task_workspace(self, result: str, status: str = "Task completed.", percent: int = 100) -> None:
        self.tasks.append({"result": result, "status": status, "percent": percent})


@dataclass
class RecordingHost:
    """A ``HostServices`` for tests: what would have been said and shown."""

    ui: RecordingUI = field(default_factory=RecordingUI)
    spoken: List[str] = field(default_factory=list)
    submitted: List[Any] = field(default_factory=list)

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def submit(self, coro: Any) -> Any:
        self.submitted.append(coro)
        return None


__all__ = [
    "MISSING_HOST",
    "HostEventSink",
    "HostServices",
    "HostUI",
    "LiveHost",
    "RecordingHost",
    "RecordingUI",
    "connect_tool_voice",
    "present_end",
    "present_start",
    "require_host",
]
