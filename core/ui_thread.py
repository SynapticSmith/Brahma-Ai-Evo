"""GUI-thread marshalling shared by the UI mixins.

`ui.BrahmaUI` is a plain Python object, not a QObject, and several of its
delegates (and `MainWindow` itself) are reached from worker threads: the
Discord gateway, the wake-word listener, the mic/audio level loop, the screen
processor, the meeting listener, the daily-briefing loop and the phone
gateway. Qt widgets may only be touched on the GUI thread, so every direct
widget call funnels through `MainWindow._run_on_ui`, which invokes the
callable inline when already on the GUI thread and otherwise queues it over a
Qt signal.

Keeping the policy here (dependency-free) means both `_ShellDelegatesMixin`
and `_MuteSendMixin` share one implementation and it can be unit-tested
without importing the heavyweight Qt multimedia stack.
"""
from __future__ import annotations


def run_on_ui(win, fn):
    """Invoke ``fn`` on the GUI thread of ``win``.

    ``win`` is a MainWindow (or any object exposing ``_run_on_ui``). When the
    host has no marshaller (lightweight test doubles), ``fn`` runs inline so
    callers keep working unchanged.
    """
    runner = getattr(win, "_run_on_ui", None)
    if runner is not None:
        runner(fn)
    else:
        fn()
