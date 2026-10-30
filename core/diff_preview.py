"""Fold a unified diff for the approval banner.

The banner already carries a bounded diff (``agent/approval_detail.py`` caps it
at 1 600 chars), but a two-hunk edit still spends most of its height on
unchanged context. This module is the view-side counterpart: it locates the
diff inside the detail text, counts additions/removals, and produces a
"changes only" form that keeps hunk headers and one line of context around each
change. It is pure Python so the parsing is tested without Qt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple

#: Lines kept either side of a change in the folded form.
DEFAULT_CONTEXT = 1


def split_detail(text: str) -> Tuple[str, str]:
    """Split ``detail`` into ``(prefix, diff)``.

    The unified diff starts at a ``--- `` line immediately followed by
    ``+++ ``. Other ``---`` lines (the edit preview's ``--- before ---``
    heading) are not diffs.
    """
    lines = str(text or "").splitlines()
    for index in range(len(lines) - 1):
        if lines[index].startswith("--- ") and lines[index + 1].startswith("+++ "):
            return "\n".join(lines[:index]), "\n".join(lines[index:])
    return "\n".join(lines), ""


def count_changes(diff: str) -> Tuple[int, int, int]:
    """``(additions, removals, hunks)`` for a unified diff."""
    additions = removals = hunks = 0
    for line in str(diff or "").splitlines():
        if line.startswith("@@"):
            hunks += 1
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            removals += 1
    return additions, removals, hunks


def fold_diff(diff: str, *, context: int = DEFAULT_CONTEXT) -> str:
    """Drop unchanged lines that are not near a change.

    A run of dropped lines becomes one ``… N unchanged line(s)`` marker instead
    of a silent gap, so the reader can tell that context was elided.
    """
    lines = str(diff or "").splitlines()
    if not lines:
        return ""
    context = max(0, int(context))
    keep: set[int] = set()
    for index, line in enumerate(lines):
        changed = (
            (line.startswith("+") and not line.startswith("+++"))
            or (line.startswith("-") and not line.startswith("---"))
            or line.startswith("@@")
            or line.startswith("+++")
            or line.startswith("---")
        )
        if changed:
            start = max(0, index - context)
            stop = min(len(lines), index + context + 1)
            keep.update(range(start, stop))

    out: List[str] = []
    skipped = 0
    for index, line in enumerate(lines):
        if index in keep:
            if skipped:
                out.append(f"… {skipped} unchanged line(s)")
                skipped = 0
            out.append(line)
        else:
            skipped += 1
    if skipped:
        out.append(f"… {skipped} unchanged line(s)")
    return "\n".join(out)


@dataclass(frozen=True)
class DiffPreview:
    """Both renderings of one approval detail plus its one-line summary."""

    full: str
    folded: str
    diff: str
    additions: int = 0
    removals: int = 0
    hunks: int = 0

    @property
    def has_diff(self) -> bool:
        return bool(self.diff)

    @property
    def summary(self) -> str:
        if not self.has_diff:
            return ""
        parts = []
        if self.additions:
            parts.append(f"+{self.additions}")
        if self.removals:
            parts.append(f"−{self.removals}")
        if not parts:
            parts.append("no line changes")
        hunks = f"{self.hunks} hunk{'s' if self.hunks != 1 else ''}"
        return " ".join(parts) + f" in {hunks}"


def preview_detail(text: str) -> DiffPreview:
    """Build the full/folded forms for one banner detail string."""
    prefix, diff = split_detail(text)
    if not diff:
        return DiffPreview(full=str(text or ""), folded=str(text or ""), diff="")
    additions, removals, hunks = count_changes(diff)
    folded = fold_diff(diff)
    if prefix:
        folded = f"{prefix}\n{folded}"
    return DiffPreview(
        full=str(text or ""),
        folded=folded,
        diff=diff,
        additions=additions,
        removals=removals,
        hunks=hunks,
    )


def changed_lines(diff: str) -> Iterable[str]:
    """Additions/removals without the file headers, for compact summaries."""
    for line in str(diff or "").splitlines():
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            yield line
