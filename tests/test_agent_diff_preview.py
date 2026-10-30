"""The banner's diff view: find the diff, count it, fold its context.

Pure Python on purpose (no Qt): the harness CI job installs only pytest.
"""
from __future__ import annotations

from core.diff_preview import (
    DEFAULT_CONTEXT,
    changed_lines,
    count_changes,
    fold_diff,
    preview_detail,
    split_detail,
)

DIFF = """--- a/app.py
+++ b/app.py
@@ -1,9 +1,9 @@
 one
 two
-old
+new
 four
 five
 six
 seven
 eight
"""


def test_split_detail_finds_a_real_unified_diff():
    prefix, diff = split_detail(
        "file: app.py\nreplace: 1 occurrence(s)\n\n" + DIFF
    )
    assert prefix == "file: app.py\nreplace: 1 occurrence(s)\n"
    assert diff.startswith("--- a/app.py")
    assert diff.rstrip().endswith("eight")


def test_split_detail_rejects_a_before_after_heading():
    text = "--- before ---\nold\n+++ after +++\nnew"
    prefix, diff = split_detail(text)
    assert diff == ""
    assert prefix == text


def test_split_detail_without_a_diff_returns_the_text():
    prefix, diff = split_detail("rule: classes.exec\ncommand: pytest -q")
    assert diff == ""
    assert prefix == "rule: classes.exec\ncommand: pytest -q"


def test_count_changes_ignores_file_headers():
    additions, removals, hunks = count_changes(DIFF)
    assert (additions, removals, hunks) == (1, 1, 1)

    multi = """--- a/x
+++ b/x
@@ -1,1 +1,2 @@
 keep
+added
@@ -9,2 +10,2 @@
-gone
+replaced
"""
    assert count_changes(multi) == (2, 1, 2)


def test_fold_keeps_context_around_changes_and_marks_the_gap():
    folded = fold_diff(DIFF)
    lines = folded.splitlines()

    assert "--- a/app.py" in lines and "+++ b/app.py" in lines
    assert any(line.startswith("@@") for line in lines)
    assert "-old" in lines and "+new" in lines
    # DEFAULT_CONTEXT lines either side survive (context keeps its leading space).
    assert " two" in lines and " four" in lines
    assert " eight" not in lines
    assert any(line.startswith("… 4 unchanged line(s)") for line in lines)
    assert len(lines) < len(DIFF.splitlines()) + 1


def test_fold_with_zero_context_keeps_only_the_changes():
    folded = fold_diff(DIFF, context=0).splitlines()
    assert "-old" in folded and "+new" in folded
    assert "two" not in folded and "four" not in folded
    assert DEFAULT_CONTEXT == 1


def test_fold_when_every_line_is_near_a_change_changes_nothing():
    diff = "--- a/x\n+++ b/x\n@@ -1,1 +1,1 @@\n-a\n+b\n"
    assert fold_diff(diff) == diff.rstrip("\n")


def test_preview_detail_exposes_both_forms_and_a_summary():
    preview = preview_detail("file: app.py\n\n" + DIFF)

    assert preview.has_diff
    assert preview.full.endswith("eight\n")
    assert preview.folded.startswith("file: app.py\n\n--- a/app.py")
    assert "… 4 unchanged line(s)" in preview.folded
    assert preview.additions == 1
    assert preview.removals == 1
    assert preview.hunks == 1
    assert preview.summary == "+1 −1 in 1 hunk"


def test_preview_detail_without_a_diff_is_the_text_unchanged():
    preview = preview_detail("command: pytest -q")
    assert not preview.has_diff
    assert preview.full == preview.folded == "command: pytest -q"
    assert preview.summary == ""


def test_changed_lines_skips_headers_and_context():
    assert list(changed_lines(DIFF)) == ["-old", "+new"]
