"""
core/confirm.py — a confirmation the model cannot forge.

THE PROBLEM WITH THE OLD GATE
    computer_settings guarded shutdown and restart like this:

        confirmed = str(params.get("confirmed", "")).lower()
        if confirmed not in ("yes", "true", "1", "confirm"):
            return "Please confirm by calling again with confirmed=yes."

    `confirmed` is a tool parameter, which means the *model* writes it. Nothing
    stops it from sending confirmed=yes on the first call, and nothing checks
    that a human was ever involved. It is a convention, not a gate — and its
    coverage was two actions, so deleting files and switching off the WiFi the
    assistant is talking over went through with no gate at all.

THE DESIGN HERE
    The confirmation token is issued by the *interface*, never by the model:

      1. An action calls `request(...)` with a callable that does the real work.
      2. This module hands the UI a banner with CONFIRM / CANCEL and returns
         IMMEDIATELY with a sentence for the model to say out loud.
      3. If — and only if — the user presses CONFIRM, the UI calls `resolve()`,
         which runs the stored callable off the Qt thread. ALWAYS ALLOW uses
         the same gate and additionally remembers the answer for the session
         (agent approvals only; see `pending_allows_always`).

    Nothing blocks. The model keeps talking while the banner is up, so this
    costs no latency at all; in fact it is cheaper than the old gate, which
    burned two tool round trips (reject, then re-call) on every shutdown.

WHAT BELONGS HERE AND WHAT DOES NOT
    Only genuinely irreversible things. Anything that can be reversed should be
    done at once and pushed onto core/undo.py instead — undo is faster than a
    question, and an assistant that asks before every action is one nobody uses.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

# A pending confirmation is abandoned after this long. Chosen to outlast a
# normal "hang on, let me look at the screen" pause without leaving a live
# shutdown button sitting on the HUD for the rest of the day.
TIMEOUT_SECONDS = 90.0


@dataclass
class _Pending:
    key:     str
    title:   str
    detail:  str
    run:     Callable[[], str]
    at:      float
    #: Set only for the blocking variant (see ``ask_blocking``). ``resolve``
    #: writes ``verdict`` and raises the event so the waiting worker thread
    #: learns the answer without polling the UI.
    waiter:  Optional[threading.Event] = None
    verdict: Optional[bool] = None
    #: True for the agent harness's blocking approval: the UI may offer
    #: "always allow" for this call. The irreversible-action flow never does.
    allow_always: bool = False
    #: Set when the user picked "always allow" (only alongside ``verdict``).
    always: bool = False
    #: What "always allow" would remember, in the user's words. Shown under the
    #: banner so the button is not a blank cheque.
    remember: str = ""


_pending: Optional[_Pending] = None
_lock = threading.Lock()

# Set once at startup by main.py. Signature: (title, detail) -> None for show,
# and () -> None for hide. Both are marshalled onto the Qt thread by the UI.
_show_cb: Optional[Callable[[str, str], None]] = None
_hide_cb: Optional[Callable[[], None]] = None
_log_cb:  Optional[Callable[[str], None]] = None


def bind(show, hide, log=None) -> None:
    """Wire this module to the HUD. Called once from main.py at startup."""
    global _show_cb, _hide_cb, _log_cb
    _show_cb, _hide_cb, _log_cb = show, hide, log


def _log(msg: str) -> None:
    if _log_cb:
        try:
            _log_cb(msg)
        except Exception:
            pass


def is_bound() -> bool:
    """True once the UI has wired this gate. Lets headless code decide
    whether an approval is even possible before asking for one."""
    return _show_cb is not None


def _on_qt_main_thread() -> bool:
    """A blocking approval on the Qt thread would deadlock: the banner's
    button handler runs on the same thread that is waiting for it."""
    try:
        from PyQt6.QtCore import QCoreApplication, QThread

        app = QCoreApplication.instance()
        return app is not None and QThread.currentThread() == app.thread()
    except Exception:
        return False


def ask_blocking_choice(
    key: str,
    detail: str,
    timeout: Optional[float] = None,
    *,
    heading: str = "",
    remember: str = "",
) -> str:
    """Like :func:`ask_blocking`, but reports *how* the human answered.

    Returns ``"allow"``, ``"always"`` (allow this exact call for the rest of
    the session) or ``"deny"``. The agent runtime uses the distinction to stop
    re-asking for a command the user has already blessed. ``heading`` replaces
    the generic "Approve <tool>?" title; ``remember`` tells the banner what the
    ALWAYS ALLOW button would actually remember.
    """
    global _pending

    if _show_cb is None or _on_qt_main_thread():
        return "deny"

    pending = _Pending(
        key=str(key),
        title=str(heading).strip() or f"Approve {key}?",
        detail=str(detail),
        run=lambda: "approved",
        at=time.monotonic(),
        waiter=threading.Event(),
        allow_always=True,
        remember=str(remember),
    )
    with _lock:
        _pending = pending

    try:
        _show_cb(pending.title, pending.detail)
    except Exception as e:
        with _lock:
            if _pending is pending:
                _pending = None
        _log(f"ERR: Could not ask for approval — {e}")
        return "deny"

    _log(f"SYS: Awaiting approval — {key}")
    wait_for = TIMEOUT_SECONDS if timeout is None else max(1.0, float(timeout))
    answered = pending.waiter.wait(wait_for)
    if not answered:
        with _lock:
            if _pending is pending:
                _pending = None
        if _hide_cb:
            try:
                _hide_cb()
            except Exception:
                pass
        _log(f"SYS: Approval timed out — {key}")
        return "deny"
    if not pending.verdict:
        return "deny"
    return "always" if pending.always else "allow"


def ask_blocking(key: str, detail: str, timeout: Optional[float] = None) -> bool:
    """Show the gate and wait for the answer.

    Used by the agent harness, where a tool call has to be resolved *now* and
    returning a sentence to the model is not an option. Must be called from a
    worker thread: on the Qt main thread it refuses (False) rather than
    deadlocking the interface. Returns False when nothing is bound, on
    timeout, and when the user cancels.
    """
    return ask_blocking_choice(key, detail, timeout) in ("allow", "always")


def request(key: str, title: str, detail: str, run: Callable[[], str]) -> str:
    """Park an irreversible action behind the on-screen gate.

    Returns the sentence the tool should hand back to the model — phrased as an
    instruction so the assistant asks the user out loud in their own language,
    rather than reading an English string verbatim."""
    global _pending

    if _show_cb is None:
        # No interface bound (headless, or a very early call). Refuse rather
        # than silently performing something irreversible.
        return (f"I cannot confirm '{title}' right now because the interface is "
                f"not available, so I have not done it.")

    with _lock:
        _pending = _Pending(key=key, title=title, detail=detail,
                            run=run, at=time.monotonic())

    try:
        _show_cb(title, detail)
    except Exception as e:
        with _lock:
            _pending = None
        return f"Could not ask for confirmation: {e}. Nothing was done."

    _log(f"SYS: Awaiting confirmation — {title}")
    return (
        f"[CONFIRMATION_PENDING] I have put a confirmation on screen for: {title}. "
        f"Say ONE short sentence in the user's own language telling them you need "
        f"them to confirm it on the HUD before you do it. Do not claim it is done."
    )


def resolve(accepted: bool, *, always: bool = False) -> None:
    """Called by the UI when the user presses CONFIRM, ALWAYS or CANCEL.

    Runs the stored callable on a worker thread — this is invoked from the Qt
    thread, and shutting the machine down from inside a button handler would
    freeze the interface on its way out."""
    global _pending

    with _lock:
        p, _pending = _pending, None

    if _hide_cb:
        try:
            _hide_cb()
        except Exception:
            pass

    if p is None:
        return

    expired = time.monotonic() - p.at > TIMEOUT_SECONDS
    if expired:
        _log(f"SYS: Confirmation expired — {p.title}")
        if p.waiter is not None:
            p.verdict = False
            p.waiter.set()
        return

    if p.waiter is not None:
        # Blocking variant (agent harness): the caller is waiting for this
        # bool and there is no callable to run.
        p.verdict = bool(accepted)
        p.always = bool(accepted and always and p.allow_always)
        p.waiter.set()
        _log(f"SYS: {'Approved' if accepted else 'Denied'} — {p.title}")
        return

    if not accepted:
        _log(f"SYS: Cancelled — {p.title}")
        return

    def _worker():
        try:
            result = p.run() or "Done."
            _log(f"SYS: Confirmed — {p.title}. {result}")
        except Exception as e:
            _log(f"ERR: {p.title} failed — {e}")

    threading.Thread(target=_worker, daemon=True,
                     name=f"confirm-{p.key}").start()


def pending_title() -> str:
    """'' when nothing is waiting. Lets an action avoid stacking two banners."""
    with _lock:
        if _pending is None:
            return ""
        if time.monotonic() - _pending.at > TIMEOUT_SECONDS:
            return ""
        return _pending.title


def pending_allows_always() -> bool:
    """True when the banner on screen belongs to a session-approvable call."""
    with _lock:
        return bool(_pending is not None and _pending.allow_always)


def pending_remember_label() -> str:
    """What the ALWAYS ALLOW button on screen would remember ('' if none)."""
    with _lock:
        return str(_pending.remember) if _pending is not None else ""
