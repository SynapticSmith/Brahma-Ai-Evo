"""Approval detail: what the banner actually shows, and what it remembers."""
from __future__ import annotations

from pathlib import Path

from agent.approval_detail import (
    MAX_DIFF_CHARS,
    ApprovalDetail,
    describe_call,
    path_scoped_approval_key,
)
from agent.permissions import PermissionPolicy, PolicyResult
from agent.runtime import AgentRuntime
from agent.spec import get as get_spec


def _decision(rule="classes.read", reason="the policy asks.", decision="ask") -> PolicyResult:
    return PolicyResult(decision=decision, reason=reason, rule=rule)


def _describe(tmp_path: Path, name: str, args: dict, rule="classes.read", decision=None):
    decision = decision or _decision(rule)
    return describe_call(name, args, get_spec(name), decision, tmp_path)


class Answers:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, name, args, detail):
        self.calls.append((name, dict(args), detail))
        return self.answers.pop(0) if self.answers else False


# -- what the banner shows -------------------------------------------------- #


def test_run_command_shows_the_command_the_cwd_and_the_rule(tmp_path: Path):
    detail = _describe(
        tmp_path,
        "run_command",
        {"command": "pytest -q", "cwd": "sub", "timeout": 60},
        rule="classes.exec",
        decision=_decision("classes.exec", "shell commands need approval."),
    )

    assert isinstance(detail, str)
    lines = detail.splitlines()
    assert lines[0] == "rule: classes.exec — shell commands need approval."
    assert "command: pytest -q" in detail
    assert f"cwd: {Path('sub')}" in detail
    assert "timeout: 60s" in detail
    assert detail.title == "Run this command?"
    assert "pytest -q" in detail.remember
    assert detail.startswith("rule:")  # why comes before what


def test_edit_file_shows_a_real_unified_diff(tmp_path: Path):
    target = tmp_path / "app.py"
    target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

    detail = _describe(
        tmp_path,
        "edit_file",
        {
            "path": "app.py",
            "old_string": "    return a - b",
            "new_string": "    return a + b",
        },
    )

    assert "file: app.py" in detail
    assert "replace: 1 occurrence(s)" in detail
    assert "\n-    return a - b" in detail
    assert "\n+    return a + b" in detail
    assert detail.title == "Edit this file?"
    assert detail.remember == "every edit_file call in this session"


def test_write_file_distinguishes_create_from_overwrite(tmp_path: Path):
    fresh = _describe(tmp_path, "write_file", {"path": "new.txt", "content": "one\ntwo"})
    assert "creates a new file" in fresh
    assert "+ one" in fresh and "+ two" in fresh
    assert "overwrites" not in fresh

    (tmp_path / "old.txt").write_text("before\n", encoding="utf-8")
    overwrite = _describe(tmp_path, "write_file", {"path": "old.txt", "content": "after\n"})
    assert "overwrites the existing file" in overwrite
    assert "\n-before" in overwrite and "\n+after" in overwrite


def test_long_diffs_are_bounded(tmp_path: Path):
    target = tmp_path / "big.txt"
    target.write_text("\n".join(f"line {i}" for i in range(400)) + "\n", encoding="utf-8")
    detail = _describe(
        tmp_path,
        "write_file",
        {"path": "big.txt", "content": "replaced\n" * 400},
    )

    assert "[diff truncated at" in detail or len(detail) < MAX_DIFF_CHARS + 400


def test_read_and_search_details_name_the_path_and_pattern(tmp_path: Path):
    read = _describe(tmp_path, "read_file", {"path": "secrets.env", "start_line": 2})
    assert "path: secrets.env" in read
    assert "lines: 2-end" in read
    assert read.title == "Read this?"

    grep = _describe(tmp_path, "grep_files", {"path": "src", "pattern": "TODO", "include": "*.py"})
    assert f"path: {Path('src')}" in grep
    assert "pattern: TODO" in grep
    assert "include: *.py" in grep


def test_unknown_tools_fall_back_to_bounded_arguments(tmp_path: Path):
    detail = describe_call(
        "native_thing",
        {"payload": "x" * 2_000},
        None,
        _decision("classes.native"),
        tmp_path,
    )
    assert "arguments:" in detail
    assert len(detail) < 1_000
    assert detail.title == "Approve native_thing?"


def test_approval_detail_is_still_a_plain_string():
    detail = ApprovalDetail("text", title="T", remember="R")
    assert detail == "text"
    assert detail.title == "T"
    assert detail.remember == "R"
    assert str(detail) == "text"


# -- what "always allow" covers -------------------------------------------- #


def test_path_keys_are_only_used_for_path_rules(tmp_path: Path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")

    assert path_scoped_approval_key("read_file", {"path": "a.txt"}, "classes.read", tmp_path) is None
    outside = path_scoped_approval_key(
        "read_file", {"path": str(tmp_path / "a.txt")}, "paths.outside_workspace", tmp_path
    )
    assert outside is not None
    assert outside.lower().endswith("a.txt")


def test_always_allow_for_an_outside_file_does_not_bless_other_files(tmp_path: Path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_text("alpha", encoding="utf-8")
    (outside / "b.txt").write_text("beta", encoding="utf-8")

    approver = Answers("always", "always")
    runtime = AgentRuntime(
        workspace=workspace,
        policy=PermissionPolicy.from_dict(
            {"classes": {"read": "allow"}, "paths": {"outside_workspace": "ask"}}
        ),
        approver=approver,
        headless=False,
    )

    assert "alpha" in runtime.execute("read_file", {"path": str(outside / "a.txt")})
    assert "alpha" in runtime.execute("read_file", {"path": str(outside / "a.txt")})
    assert len(approver.calls) == 1

    # A different outside path is a different exception and must ask again.
    assert "beta" in runtime.execute("read_file", {"path": str(outside / "b.txt")})
    assert len(approver.calls) == 2

    name, args, detail = approver.calls[0]
    assert "rule: paths.outside_workspace" in detail
    assert detail.title == "Read this?"
    assert detail.remember.startswith("this path (")
    assert str(outside / "a.txt") in detail


def test_a_class_level_ask_is_remembered_for_the_tool_not_one_file(tmp_path: Path):
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "b.txt").write_text("beta", encoding="utf-8")

    approver = Answers("always")
    runtime = AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict({"classes": {"read": "ask"}}),
        approver=approver,
        headless=False,
    )

    assert "alpha" in runtime.execute("read_file", {"path": "a.txt"})
    assert "beta" in runtime.execute("read_file", {"path": "b.txt"})
    assert len(approver.calls) == 1
    assert approver.calls[0][2].remember == "every read_file call in this session"
