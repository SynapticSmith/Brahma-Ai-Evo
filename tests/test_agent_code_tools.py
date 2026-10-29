"""The six file/process primitives, exercised against a temporary workspace."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent import code_tools
from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from agent.spec import ToolContext, get as get_spec


@pytest.fixture()
def context(tmp_path: Path) -> ToolContext:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def hello():\n    return 'world'\n\n\ndef goodbye():\n    return 'bye'\n",
        encoding="utf-8",
    )
    return ToolContext(workspace=tmp_path)


def test_all_six_tools_are_registered():
    for name in ("read_file", "write_file", "edit_file", "glob_files", "grep_files", "run_command"):
        assert get_spec(name) is not None, name
        assert code_tools.is_code_tool(name)


def test_read_numbers_lines_and_honours_range(context: ToolContext):
    text = code_tools.read_file({"path": "src/app.py", "start_line": 1, "end_line": 2}, context)
    assert "src/app.py (lines 1-2 of 6)" in text
    assert "    1| def hello():" in text
    assert "    3|" not in text


def test_read_rejects_binary_and_missing(context: ToolContext):
    assert "does not exist" in code_tools.read_file({"path": "nope.py"}, context)
    (context.workspace / "blob.bin").write_bytes(b"\x00\x01\x02")
    assert "binary" in code_tools.read_file({"path": "blob.bin"}, context)


def test_read_refuses_a_directory(context: ToolContext):
    assert "is a directory" in code_tools.read_file({"path": "src"}, context)


def test_write_creates_and_reports_new_file(context: ToolContext):
    result = code_tools.write_file({"path": "new/deep/file.txt", "content": "alpha\n"}, context)
    assert "Created new/deep/file.txt" in result
    assert (context.workspace / "new" / "deep" / "file.txt").read_text(encoding="utf-8") == "alpha\n"


def test_write_can_refuse_to_overwrite(context: ToolContext):
    result = code_tools.write_file(
        {"path": "src/app.py", "content": "x", "overwrite": False}, context
    )
    assert "already exists" in result
    assert "def hello" in (context.workspace / "src" / "app.py").read_text(encoding="utf-8")


def test_write_overwrite_includes_a_diff(context: ToolContext):
    result = code_tools.write_file(
        {"path": "src/app.py", "content": "def hello():\n    return 'mars'\n"}, context
    )
    assert "Overwrote src/app.py" in result
    assert "+    return 'mars'" in result


def test_edit_replaces_a_unique_string(context: ToolContext):
    result = code_tools.edit_file(
        {"path": "src/app.py", "old_string": "return 'world'", "new_string": "return 'mars'"},
        context,
    )
    assert "1 replacement" in result
    assert "mars" in (context.workspace / "src" / "app.py").read_text(encoding="utf-8")
    assert "world" not in (context.workspace / "src" / "app.py").read_text(encoding="utf-8")


def test_edit_refuses_an_ambiguous_match(context: ToolContext):
    result = code_tools.edit_file(
        {"path": "src/app.py", "old_string": "return", "new_string": "yield"}, context
    )
    assert "appears 2 times" in result
    assert "replace_all=true" in result


def test_edit_replace_all_is_explicit_opt_in(context: ToolContext):
    result = code_tools.edit_file(
        {"path": "src/app.py", "old_string": "return", "new_string": "yield", "replace_all": True},
        context,
    )
    assert "2 replacements" in result


def test_edit_explains_a_whitespace_mismatch(context: ToolContext):
    # The text is there but with trailing spaces on the needle; the handler
    # must say so instead of a bare "not found".
    result = code_tools.edit_file(
        {"path": "src/app.py", "old_string": "return 'bye'   ", "new_string": "x"}, context
    )
    assert "was not found" in result
    assert "whitespace differs" in result


def test_edit_missing_string_points_back_at_the_file(context: ToolContext):
    result = code_tools.edit_file(
        {"path": "src/app.py", "old_string": "does_not_exist()", "new_string": "x"}, context
    )
    assert "was not found" in result
    assert "Read the file" in result


def test_glob_finds_by_pattern_and_skips_noise(context: ToolContext):
    (context.workspace / "node_modules").mkdir()
    (context.workspace / "node_modules" / "junk.py").write_text("x", encoding="utf-8")
    result = code_tools.glob_files({"pattern": "**/*.py"}, context)
    assert "src/app.py" in result
    assert "junk.py" not in result


def test_glob_reports_no_matches(context: ToolContext):
    assert "No files matching" in code_tools.glob_files({"pattern": "**/*.zzz"}, context)


def test_grep_returns_path_line_text(context: ToolContext):
    result = code_tools.grep_files({"pattern": "def hello", "include": "**/*.py"}, context)
    assert "src/app.py:1:" in result
    assert "def hello" in result


def test_grep_rejects_an_invalid_regex(context: ToolContext):
    assert "Invalid regular expression" in code_tools.grep_files({"pattern": "["}, context)


def test_run_command_reports_exit_code_and_streams(context: ToolContext):
    result = code_tools.run_command(
        {"command": f'"{sys.executable}" -c "print(123)"'}, context
    )
    assert "exit_code: 0" in result
    assert "123" in result


def test_run_command_reports_failure(context: ToolContext):
    result = code_tools.run_command(
        {"command": f'"{sys.executable}" -c "import sys; sys.exit(4)"'}, context
    )
    assert "exit_code: 4" in result


def test_run_command_rejects_a_missing_cwd(context: ToolContext):
    assert "does not exist" in code_tools.run_command({"command": "echo hi", "cwd": "ghost"}, context)


def test_run_command_times_out(context: ToolContext):
    result = code_tools.run_command(
        {"command": f'"{sys.executable}" -c "import time; time.sleep(3)"', "timeout": 1},
        context,
    )
    assert "timed out" in result


# --------------------------------------------------------------------------- #
# Policy integration: handlers are safe, the runtime is the gate
# --------------------------------------------------------------------------- #

def _runtime(tmp_path: Path, **policy_overrides) -> AgentRuntime:
    data = {"classes": {"read": "allow", "write": "allow", "exec": "allow"}}
    data.update(policy_overrides)
    return AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict(data),
        approver=None,
        headless=True,
    )


def test_runtime_blocks_outside_workspace_writes(tmp_path: Path):
    runtime = _runtime(tmp_path)
    result = runtime.execute("write_file", {"path": "../escape.txt", "content": "x"})
    assert result.lower().startswith("permission denied")
    assert not (tmp_path.parent / "escape.txt").exists()


def test_runtime_asks_and_honours_a_refusal(tmp_path: Path):
    runtime = _runtime(tmp_path, classes={"write": "ask"})
    runtime.approver = lambda name, args, detail: False
    result = runtime.execute("write_file", {"path": "x.txt", "content": "x"})
    assert result.lower().startswith("not run:")
    assert not (tmp_path / "x.txt").exists()


def test_runtime_runs_when_approved(tmp_path: Path):
    runtime = _runtime(tmp_path, classes={"write": "ask"})
    seen = []

    def approve(name, args, detail):
        seen.append((name, detail))
        return True

    runtime.approver = approve
    result = runtime.execute("write_file", {"path": "x.txt", "content": "hello"})
    assert "Created x.txt" in result
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "hello"
    assert seen and "x.txt" in seen[0][1] and "classes.write" in seen[0][1]


def test_runtime_reports_unknown_tools(tmp_path: Path):
    runtime = _runtime(tmp_path)
    assert "Unknown tool" in runtime.execute("not_a_tool", {})


def test_runtime_delegates_native_tools(tmp_path: Path):
    runtime = _runtime(tmp_path)
    calls = []

    def native(name, args):
        calls.append((name, args))
        return f"native:{name}"

    assert runtime.execute("do_thing", {"a": 1}, native_dispatch=native) == "native:do_thing"
    assert calls == [("do_thing", {"a": 1})]
