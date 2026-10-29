"""The single dispatch boundary: policy, approval, then the handler.

Both entry points — the GUI's Gemini live path (``core/tool_exec.py``) and the
agent loop (``agent/loop.py``) — go through :func:`AgentRuntime.execute`. That
is deliberate. If permission enforcement lived in the loop, a voice turn could
bypass it; if it lived in each handler, one forgotten decorator would be a
hole. One path, one decision point.

Approval is a callback so the same runtime works in three places:

* GUI      -> ``core.confirm.ask_blocking_choice`` (HUD banner; can return
  "always", which the runtime remembers for this process)
* CLI/TTY  -> a prompt on stdin (``a`` means "always" for this session)
* CI/headless -> ``None``, which turns ``ask`` into ``deny``

Nothing here consults the model.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from . import code_tools  # noqa: F401  (import registers the six tools)
from .approval_detail import describe_call, path_scoped_approval_key
from .permissions import DENY, PermissionPolicy, PolicyResult
from .spec import ToolContext, ToolSpec, execute as execute_spec, get as get_spec

Approver = Callable[[str, Dict[str, Any], str], Any]
EventSink = Callable[[Dict[str, Any]], None]

#: Answers an approver may return. A plain ``bool`` still works (older hosts),
#: but the HUD returns the string form so "always allow" survives.
_APPROVE_VALUES = {"allow", "approved", "yes", "true", "always", "always_allow"}

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


def gui_approver(name: str, args: Dict[str, Any], detail: str) -> Any:
    """Route an approval to the HUD, if this process has one bound.

    Returns ``"allow"``, ``"always"`` or ``"deny"`` so the runtime can remember
    a session approval; falls back to ``False`` when no HUD is bound. The
    detail object built by :mod:`agent.approval_detail` also carries the banner
    heading and what ALWAYS ALLOW would remember; plain strings still work.
    """
    try:
        from core import confirm

        if confirm.is_bound():
            return confirm.ask_blocking_choice(
                name,
                detail,
                heading=getattr(detail, "title", ""),
                remember=getattr(detail, "remember", ""),
            )
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
    #: Set by the GUI shortcut / CLI Ctrl-C. A loop reads it between turns and
    #: calls; killing a thread outright would leave a half-written file and a
    #: half-run command behind.
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    #: Keys the human said "always allow" to in this process. Never persisted:
    #: a new session asks again. See ``_approval_key``.
    session_allow: set = field(default_factory=set, repr=False)
    #: How often ``execute`` may stat the policy file. Reading a policy per
    #: call would be a syscall on every tool; reading it never means a running
    #: agent keeps the rules it started with. 1 s is invisible to a person and
    #: cheap next to a tool call.
    policy_check_interval: float = 1.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _policy_mtime_ns: Optional[int] = field(default=None, repr=False)
    _policy_checked_at: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        # Record the mtime of the file this policy came from, so the first
        # edit after construction is detected instead of being absorbed as a
        # baseline.
        source = getattr(self.policy, "source", None)
        if source is not None:
            try:
                self._policy_mtime_ns = Path(source).stat().st_mtime_ns
            except OSError:
                pass

    def cancel(self) -> None:
        """Ask the running loop (if any) to stop after the current call."""
        self.cancel_event.set()

    def reset_cancel(self) -> None:
        """Clear a previous cancellation before starting a new turn."""
        self.cancel_event.clear()

    def reload_policy(self, *, force: bool = False) -> Optional[PermissionPolicy]:
        """Adopt an edited policy file without restarting Brahma.

        The alternative — restart the app to change one rule — is exactly the
        friction that makes people edit `deny` into `allow` in the wrong file
        or stop using the policy at all. Throttled by
        ``policy_check_interval`` and skipped when the policy came from
        built-in defaults (``source is None``).

        A malformed edit is not fatal: the last good policy stays in force and
        a ``policy_reload`` event with ``ok=False`` is emitted, so a typo can
        never silently widen or disable the rules. The bad mtime is recorded so
        the same broken file is not re-parsed on every call; the next edit
        retries.
        """
        source = getattr(self.policy, "source", None)
        if source is None:
            return None

        now = time.monotonic()
        with self._lock:
            if not force and (now - self._policy_checked_at) < self.policy_check_interval:
                return None
            self._policy_checked_at = now

        try:
            mtime_ns = Path(source).stat().st_mtime_ns
        except OSError:
            return None

        with self._lock:
            if not force and mtime_ns == self._policy_mtime_ns:
                return None
            self._policy_mtime_ns = mtime_ns

        try:
            reloaded = PermissionPolicy.load(source)
        except (OSError, ValueError) as exc:  # PolicyError is a ValueError
            self._emit(
                "policy_reload",
                {"ok": False, "source": str(source), "error": str(exc)},
            )
            return None

        if reloaded.source is None or Path(reloaded.source) != Path(source):
            # The file vanished between stat and read and `load` fell back to
            # another candidate. Keep the policy this runtime already trusts.
            self._emit(
                "policy_reload",
                {"ok": False, "source": str(source), "error": "policy file disappeared"},
            )
            return None

        self.policy = reloaded
        self._emit(
            "policy_reload",
            {"ok": True, "source": str(reloaded.source)},
        )
        return reloaded

    def _approval_key(
        self,
        name: str,
        args: Mapping[str, Any],
        spec: Optional[ToolSpec],
        rule: str = "",
    ) -> str:
        """What "always allow" covers.

        A shell command is keyed by the exact command, because approving
        ``pytest -q`` must not approve ``del``. A path-based exception
        (``paths.outside_workspace`` / ``paths.protected``) is keyed by the
        resolved path, because approving one outside file must not bless every
        outside file for the rest of the session. A class-level ask is keyed by
        the tool name: the policy is asking about the tool itself.
        """
        tool_class = spec.tool_class if spec else "native"
        if tool_class == "exec":
            command = str(args.get("command") or args.get("cmd") or "").strip()
            return f"{name}:{command}"
        scoped = path_scoped_approval_key(name, args, rule, self.workspace)
        if scoped:
            return f"{name}:{scoped}"
        return name

    def allow_for_session(
        self,
        name: str,
        args: Optional[Mapping[str, Any]] = None,
        *,
        rule: str = "",
    ) -> None:
        """Remember an approval for the rest of this process."""
        spec = get_spec(name)
        with self._lock:
            self.session_allow.add(self._approval_key(name, dict(args or {}), spec, rule))

    def clear_session_approvals(self) -> None:
        with self._lock:
            self.session_allow.clear()

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
        # Cheap, throttled check: a policy edit lands without a restart.
        self.reload_policy()
        spec, decision = self.decide(name, args)
        key = self._approval_key(name, args, spec, decision.rule)

        if decision.needs_approval:
            # A session approval only replaces the *human*, never a policy
            # decision: `requires_approval` tools (irreversible actions) ask
            # every time by construction.
            remembered = False
            if decision.rule != "tool.requires_approval":
                with self._lock:
                    remembered = key in self.session_allow
            if remembered:
                self._emit(
                    "permission",
                    {"name": name, "decision": "allow", "rule": "session.allow",
                     "reason": "approved earlier in this session."},
                )
            else:
                detail = describe_call(name, args, spec, decision, self.workspace)
                self._emit("permission", {"name": name, "decision": "ask", "rule": decision.rule, "reason": decision.reason})
                approved = False
                always = False
                if self.approver is not None:
                    try:
                        answer = self.approver(name, args, detail)
                    except Exception as exc:
                        self._emit("permission", {"name": name, "decision": "error", "error": str(exc)})
                        answer = False
                    if isinstance(answer, str):
                        marker = answer.strip().lower()
                        approved = marker in _APPROVE_VALUES
                        always = marker in ("always", "always_allow")
                    else:
                        approved = bool(answer)
                    if approved and always:
                        with self._lock:
                            self.session_allow.add(key)
                        self._emit(
                            "permission",
                            {"name": name, "decision": "always", "rule": decision.rule},
                        )
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
            # Carry the flag across a re-configure: a loop started before the
            # call holds a reference to this event, and a Stop press must still
            # reach it. Session approvals survive too, for the same reason.
            cancel_event=current.cancel_event,
            session_allow=current.session_allow,
        )
        return _current


def cancel() -> None:
    """Ask the current runtime's loop to stop (GUI shortcut, CLI handler)."""
    _current.cancel()


def reset_cancel() -> None:
    """Clear a stale cancellation before a new user turn."""
    _current.reset_cancel()


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
