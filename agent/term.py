"""ANSI colour for the CLI approval prompt, and nothing else.

A terminal approval is the one place where colour carries meaning: in a
unified diff, added and removed lines are hard to scan as plain text. The HUD
already highlights them; the TTY prompt did not. This module keeps the rule
small and opt-out-able:

* colour is only on when the destination is a TTY (or ``FORCE_COLOR`` says it
  is), never when ``NO_COLOR`` is set, ``TERM=dumb``, or the stream is a pipe;
* on Windows the console is switched into VT mode first, so a legacy console
  gets plain text instead of literal escape sequences;
* the plain branch is byte-identical to the pre-colour prompt, so redirected
  output and log capture do not change.

Rendering is pure: :func:`colour_detail` and :func:`approval_prompt` take text
and return text. Only :func:`supports_color` and :func:`prepare_stream` look at
the environment or a stream, which is what makes the rest testable.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Optional

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"

#: ``ENABLE_VIRTUAL_TERMINAL_PROCESSING`` for ``SetConsoleMode``.
_WINDOWS_VT = 0x0004

#: Keys whose value is worth showing on an approval line. Everything else is
#: left alone, so a diff's context lines and free text are not reflowed.
_LABELS = frozenset(
    {
        "command",
        "cwd",
        "timeout",
        "file",
        "path",
        "pattern",
        "include",
        "lines",
        "write",
        "replace",
    }
)


def paint(text: str, code: str) -> str:
    """Wrap one run of text in ``code`` and reset. Empty text stays empty."""
    if not text:
        return text
    return f"{code}{text}{RESET}"


def _forced() -> bool:
    return str(os.environ.get("FORCE_COLOR") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def supports_color(stream: Optional[Any] = None) -> bool:
    """Whether ANSI should be considered for ``stream`` (default stderr).

    This is the policy half: environment first, then ``isatty``. It does not
    know whether a Windows console can actually process the sequences; call
    :func:`prepare_stream` for that.
    """
    if "NO_COLOR" in os.environ:
        return False
    if str(os.environ.get("TERM") or "").strip().lower() == "dumb":
        return False
    if _forced():
        return True
    stream = stream if stream is not None else sys.stderr
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def prepare_stream(stream: Optional[Any] = None) -> bool:
    """Enable VT processing on a Windows console; ``True`` elsewhere.

    A legacy console rejects ``SetConsoleMode``; returning ``False`` there is
    the point, because printing raw escapes into it is worse than no colour.
    ``FORCE_COLOR`` skips the probe: the user has asserted that the destination
    understands ANSI (for example a pipe into ``less -R``).
    """
    if os.name != "nt":
        return True
    if _forced():
        return True
    stream = stream if stream is not None else sys.stderr
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(stream.fileno())
        kernel32 = ctypes.windll.kernel32
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | _WINDOWS_VT))
    except Exception:
        return False


def colour_enabled(stream: Optional[Any] = None) -> bool:
    """Both halves: policy says yes *and* the destination can render it."""
    return supports_color(stream) and prepare_stream(stream)


def _colour_line(line: str) -> str:
    # Diff headers and the preview heading are metadata, not changes.
    if line.startswith(("--- ", "+++ ")):
        return paint(line, DIM)
    if line.startswith("@@"):
        return paint(line, CYAN)
    if line.startswith("+"):
        return paint(line, GREEN)
    if line.startswith("-"):
        return paint(line, RED)
    if line.startswith("rule:"):
        return paint(line, BOLD + YELLOW)
    label, separator, rest = line.partition(":")
    if separator and label in _LABELS:
        return paint(label + separator, BOLD) + rest
    return line


def colour_detail(detail: str) -> str:
    """Colour a diff-bearing approval detail line by line.

    The function is a view: it never rewrites a line's text, only wraps it, so
    the same string minus escapes is still the exact prompt a person expects.
    """
    return "\n".join(_colour_line(line) for line in str(detail).splitlines())


def approval_prompt(detail: str, name: str, *, colour: bool) -> str:
    """The complete stderr text for one TTY approval.

    Plain mode is exactly the historical prompt: a blank line, ``[approval]``
    and the detail, then the question with no trailing newline (the answer is
    typed on the same line).
    """
    marker = paint("[approval]", BOLD + MAGENTA) if colour else "[approval]"
    body = colour_detail(detail) if colour else detail
    question = f"Allow '{name}'? [y/N/a=always] "
    if colour:
        question = paint(question, BOLD)
    return f"\n{marker} {body}\n{question}"
