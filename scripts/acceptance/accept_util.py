"""Shared result helpers for the ``accept_*.py`` acceptance harnesses.

Every harness prints one line per criterion and then a single terminal RESULT
line, and `scripts/precommit_check.py` gates on the literal substring
``ALL CRITERIA PASS`` in that line. That contract used to be copy-pasted into
each harness, so a change to one -- the pass/fail prefix, the failure summary,
or the terminal wording the gate greps for -- could silently diverge. It lives
here once instead.

A harness does:

    from accept_util import check, section, report
    ...
    check("the thing works", ok, detail)
    ...
    return report()          # prints the RESULT line, returns the exit code

``report()`` must be called exactly once, last, so the terminal line the
pre-commit gate scrapes is emitted exactly once. ``FAILURES`` is module state;
the seven harnesses are separate processes, so there is no cross-talk.
"""

from __future__ import annotations

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    """Record and print one criterion. A false `ok` is appended for `report`."""
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def section(name: str) -> None:
    print(f"\n{name}")


def report() -> int:
    """Print the terminal RESULT line and return the process exit code.

    The exact "RESULT: ALL CRITERIA PASS" string is the contract that
    scripts/precommit_check.py scrapes; keep it intact.
    """
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILED -> {FAILURES}")
        return 1
    print("RESULT: ALL CRITERIA PASS")
    return 0
