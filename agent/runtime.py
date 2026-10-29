"""The single dispatch boundary: policy, approval, then the handler.

Both entry points — the GUI's Gemini live path (``core/tool_exec.py``) and the
agent loop (``agent/loop.py``) — go through :func:`AgentRuntime.execute`. That
is deliberate. If permission enforcement lived in the loop, a voice turn could
bypass it; if it lived in each handler, one forgotten decorator would be a
hole. One path, one decision point.

Approval is a callback so the same runtime works in three places:

* GUI      -> ``core.confirm.ask_blocking`` (HUD banner, blocks a worker thread)
* CLI/TTY  -> a prompt on stdin
* CI/headless -> ``None``, which turns ``ask`` into ``deny``

Nothing here consults the model.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from . import code_tools  # noqa: F401  (import registers the six tools)
from .permissions import DENY, PermissionPolicy, PolicyResult
from .spec import ToolContext, ToolSpec, execute as execute_spec, get as get_spec

Approver = Callable[[str, Dict[str, Any], str], bool]
EventSink = Callable[[Dict[str, Any]], None]

_REPO_ROOT = Path(__file__).resolve().parent.parent


def default_workspace() -> Path:
    """The folder an agent works in when nobody says otherwise.

    Mirrors the developer-mode workspace the GUI already uses, so "the agent's
    workspace" and "the folder the user pointed Brahma at" are the same place.
    """
    env = os.getenv("BRAHMA_AGENT_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()
    try:
        settings = json.loads((_REPO_ROOT / "config" / "app_settings.json").read_text(encoding="utf-8"))
        configured = str(settings.get("developer_mode_workspace") or "").strip()
        if configured:
            path = Path(configured).expanduser()
            if path.exists():
                return path.resolve()
    except (OSError, ValueError):
        pass
    return Path.cwd().resolve()


def gui_approver(name: str, args: Dict[str, Any], detail: str) -> bool:
    """Route an approval to the HUD, if this process has one bound."""
    try:
        from core import confirm

        if confirm.is_bound():
            return confirm.ask_blocking(name, detail)
    except Exception:
        pass
    return False


#: Backwards-compatible private alias.
_gui_approver = gui_approver


def _default_headless() -> bool:
    try:
        from core import confirm

        return not confirm.is_bound()
    except Exception:
        return True


@dataclass
class AgentRuntime:
    workspace: Path = field(default_factory=default_workspace)
    policy: PermissionPolicy = field(default_factory=PermissionPolicy.load)
    approver: Optional[Approver] = field(default_factory=lambda: _gui_approver)
    headless: bool = True
    timeout: float = 120.0
    max_output_chars: int = 20_000
    event_sink: Optional[EventSink] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def context(self) -> ToolContext:
        return ToolContext(
            workspace=self.workspace,
            timeout=self.timeout,
            max_output_chars=self.max_output_chars,
            policy=self.policy,
        )

    # -- policy ------------------------------------------------------------

    def decide(self, name: str, args: Optional[Mapping[str, Any]] = None) -> tuple[ToolSpec | None, PolicyResult]:
        spec = get_spec(name)
        tool_class = spec.tool_class if spec else "native"
        # "Headless" means *no human can be asked*. An injected approver makes
        # that false, whatever the flag says: the CLI passes headless=True and
        # approver=None, the TTY passes headless=False and a prompt, and the
        # GUI passes headless=False plus the HUD.
        no_approver = self.approver is None
        result = self.policy.evaluate(
            name,
            dict(args or {}),
            tool_class=tool_class,
            context_workspace=self.workspace,
            headless=self.headless and no_approver,
            requires_approval=bool(spec and spec.requires_approval),
        )
        return spec, result

    def _emit(self, kind: str, payload: Dict[str, Any]) -> None:
        sink = self.event_sink
        if sink is None:
            return
        try:
            sink({"type": kind, **payload})
        except Exception:
            pass

    # -- dispatch ----------------------------------------------------------

    def execute(
        self,
        name: str,
        args: Optional[Mapping[str, Any]] = None,
        *,
        native_dispatch: Optional[Callable[[str, Dict[str, Any]], str]] = None,
        call_id: str = "call_agent",
    ) -> str:
        """Run one tool call under policy, returning model-readable text."""
        args = dict(args or {})
        spec, decision = self.decide(name, args)

        if decision.needs_approval:
            detail = f"{decision.reason}\n\n{name}({json.dumps(args, ensure_ascii=False, default=str)[:600]})"
            self._emit("permission", {"name": name, "decision": "ask", "rule": decision.rule, "reason": decision.reason})
            approved = False
            if self.approver is not None:
                try:
                    approved = bool(self.approver(name, args, detail))
                except Exception as exc:
                    self._emit("permission", {"name": name, "decision": "error", "error": str(exc)})
                    approved = False
            if not approved:
                self._emit("permission", {"name": name, "decision": "denied"})
                return (
                    f"Not run: '{name}' needs approval and it was not given "
                    f"({decision.rule}). Tell the user what this would do and why "
                    "it is needed; do not retry it in this turn."
                )

        if decision.decision == DENY:
            self._emit("permission", {"name": name, "decision": "deny", "rule": decision.rule, "reason": decision.reason})
            return decision.for_model(name)

        self._emit("tool_start", {"name": name, "call_id": call_id, "args": args})
        if spec is not None:
            output = execute_spec(spec, args, self.context())
        elif native_dispatch is not None:
            try:
                output = native_dispatch(name, args)
            except Exception as exc:  # noqa: BLE001 - tool boundary
                output = f"Tool '{name}' failed: {type(exc).__name__}: {exc}"
        else:
            output = f"Unknown tool '{name}'. Registered tools: see the catalog."
        self._emit("tool_end", {"name": name, "call_id": call_id, "chars": len(output or "")})
        return output or ""


# --------------------------------------------------------------------------- #
# Process-wide current runtime
# --------------------------------------------------------------------------- #

_current = AgentRuntime(headless=_default_headless())
_current_lock = threading.Lock()


def configure(
    *,
    workspace: Optional[str | Path] = None,
    policy: Optional[PermissionPolicy] = None,
    approver: Optional[Approver] = None,
    headless: Optional[bool] = None,
    timeout: Optional[float] = None,
    max_output_chars: Optional[int] = None,
    event_sink: Optional[EventSink] = None,
    reset_approver: bool = False,
) -> AgentRuntime:
    """Replace the process-wide runtime. Returns it for convenience."""
    global _current
    with _current_lock:
        current = _current
        _current = AgentRuntime(
            workspace=Path(workspace).expanduser().resolve() if workspace else current.workspace,
            policy=policy if policy is not None else current.policy,
            approver=(None if reset_approver else (approver if approver is not None else current.approver)),
            headless=current.headless if headless is None else bool(headless),
            timeout=current.timeout if timeout is None else float(timeout),
            max_output_chars=current.max_output_chars if max_output_chars is None else int(max_output_chars),
            event_sink=event_sink if event_sink is not None else current.event_sink,
        )
        return _current


def current() -> AgentRuntime:
    return _current


def execute(
    name: str,
    args: Optional[Mapping[str, Any]] = None,
    *,
    native_dispatch: Optional[Callable[[str, Dict[str, Any]], str]] = None,
    call_id: str = "call_agent",
) -> str:
    return _current.execute(name, args, native_dispatch=native_dispatch, call_id=call_id)


def policy_note() -> str:
    """One line for the system prompt describing what needs approval."""
    runtime = _current
    classes = runtime.policy.data.get("classes") or {}
    headless = runtime.policy.data.get("headless") or {}
    bits = [f"{name}={value}" for name, value in sorted(classes.items())]
    note = "Permission policy: " + ", ".join(bits) + "."
    if runtime.headless:
        bits = [f"{name}={value}" for name, value in sorted(headless.items())]
        note += " No approver is available, so: " + ", ".join(bits) + "."
    return note
