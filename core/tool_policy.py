"""
Tool Execution Policy, Shell & Python REPL Containment (WS-5, SEC-10).

Part of Security Architecture Phase 2.
Implements:
1. Tool capability mapping and policy enforcement (least privilege per tool).
2. Shell command allowlist (`pytest`, `npm test`, `cargo build`, `git diff`, `git status`).
3. Sensitive file access protection (blocks shell commands touching keys/config/DPAPI).
4. Process-contained `bash` and `python_repl` runners via `run_captured` and Job Objects.
5. Strict environment scrubbing and closed stdin for tool subprocesses.
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple

from agent.process import run_captured
from agent.sandbox import SandboxConfig

logger = logging.getLogger("ToolPolicy")

# Command prefixes permitted without interactive approval
ALLOWED_SHELL_COMMANDS: Set[str] = {
    "pytest",
    "py -3.11 -m pytest",
    "npm test",
    "cargo test",
    "cargo build",
    "git status",
    "git diff",
    "git log",
    "dir",
    "ls",
    "echo",
}

# Substrings strictly forbidden in shell commands to prevent credential or audit tampering
BLOCKED_SHELL_SUBSTRINGS: Set[str] = {
    "api_keys.json",
    "smart_home.key",
    "audit.jsonl",
    "BRAHMA_DPAPI",
    "release_state.json",
    ".git/config",
    "id_rsa",
    "id_ed25519",
}


def is_shell_command_safe(command: str) -> Tuple[bool, str]:
    """Evaluates whether a shell command matches the allowlist and touches no sensitive files."""
    cmd_clean = command.strip()
    cmd_lower = cmd_clean.lower()

    # 1. Block commands referencing sensitive paths or keys
    for blocked in BLOCKED_SHELL_SUBSTRINGS:
        if blocked.lower() in cmd_lower:
            return False, f"Command contains forbidden sensitive path reference: '{blocked}'."

    # 2. Check allowlist
    for allowed in ALLOWED_SHELL_COMMANDS:
        if cmd_clean == allowed or cmd_clean.startswith(allowed + " "):
            return True, f"Command matches allowlist prefix '{allowed}'."

    return False, "Command is outside the safe allowlist; requires explicit human approval."


def get_required_capabilities(tool_name: str, args: Dict[str, Any]) -> List[str]:
    """Returns the capability strings required to invoke the tool."""
    tool_lower = tool_name.lower().strip()

    if tool_lower in ("bash", "run_command", "shell"):
        return ["process.spawn"]
    elif tool_lower in ("python_repl", "repl"):
        return ["python.repl"]
    elif tool_lower in ("read_file", "file_read"):
        path = str(args.get("path") or args.get("file") or "*")
        return [f"fs.read:{path}"]
    elif tool_lower in ("write_file", "file_write", "edit_file", "delete_file"):
        path = str(args.get("path") or args.get("file") or "*")
        return [f"fs.write:{path}"]
    elif tool_lower.startswith("mcp_"):
        return ["mcp.call"]
    elif tool_lower in ("send_message", "send_chat"):
        return ["net.send"]
    elif tool_lower in ("skill_forge", "forge_skill"):
        return ["skill.forge"]

    return []


def evaluate_tool_call(
    tool_name: str,
    args: Dict[str, Any],
    grant: Optional[Dict[str, Any]] = None,
    is_tainted: bool = False,
) -> Tuple[bool, str]:
    """Evaluates whether a tool call may proceed under capability and taint policy."""
    tool_lower = tool_name.lower().strip()

    # 1. Shell commands check
    if tool_lower in ("bash", "run_command", "shell"):
        cmd = str(args.get("command") or args.get("cmd") or "")
        safe, reason = is_shell_command_safe(cmd)
        if not safe and "forbidden sensitive path" in reason:
            return False, f"E_DENIED: {reason}"

        # If outside allowlist, require process.spawn capability or untainted grant
        if not safe:
            if not grant:
                return False, f"E_NO_GRANT: {reason}"
            caps = set(grant.get("capabilities", []))
            if "process.spawn" not in caps and "*" not in caps:
                return False, "E_DENIED: Missing required capability 'process.spawn'."

    # 2. General capability check if grant provided
    required_caps = get_required_capabilities(tool_lower, args)
    if grant and required_caps:
        granted_caps = set(grant.get("capabilities", []))
        if "*" not in granted_caps:
            for req in required_caps:
                matched = any(
                    fnmatch.fnmatch(req, g) or g == "*" or req == g
                    for g in granted_caps
                )
                if not matched:
                    return False, f"E_NO_GRANT: Lacks required capability '{req}'."

    # 3. Tainted state restrictions
    if is_tainted and tool_lower in ("write_file", "delete_file", "skill_forge", "bash"):
        if not grant or not grant.get("approved_by"):
            return False, f"E_DENIED: State-changing tool '{tool_lower}' blocked in tainted context."

    return True, "Tool execution permitted."


def execute_hardened_bash(
    command: str,
    cwd: Optional[Path] = None,
    timeout: float = 60.0,
    grant: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Executes a shell command inside Windows Job Object with scrubbed environment."""
    ok, reason = evaluate_tool_call("bash", {"command": command}, grant=grant)
    if not ok:
        return {
            "success": False,
            "stdout": "",
            "stderr": reason,
            "returncode": 1,
            "timed_out": False,
        }

    sandbox = SandboxConfig(env_scrub=True)
    target_cwd = Path(cwd).resolve() if cwd is not None else Path.cwd().resolve()
    res = run_captured(
        command,
        cwd=target_cwd,
        timeout=timeout,
        sandbox=sandbox,
    )

    return {
        "success": res.returncode == 0 and not res.timed_out,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "returncode": res.returncode,
        "timed_out": res.timed_out,
    }


def execute_hardened_python_repl(
    code: str,
    timeout: float = 30.0,
    grant: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Executes Python code in an isolated subprocess with scrubbed environment and closed stdin."""
    ok, reason = evaluate_tool_call("python_repl", {"code": code}, grant=grant)
    if not ok:
        return {
            "success": False,
            "stdout": "",
            "stderr": reason,
            "returncode": 1,
            "timed_out": False,
        }

    with tempfile.TemporaryDirectory(prefix="brahma_repl_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        script_path = tmp_path / "repl_script.py"
        script_path.write_text(code, encoding="utf-8")

        cmd = f'"{sys.executable}" "{script_path}"'
        sandbox = SandboxConfig(env_scrub=True)
        res = run_captured(
            cmd,
            cwd=tmp_path,
            timeout=timeout,
            sandbox=sandbox,
        )

        return {
            "success": res.returncode == 0 and not res.timed_out,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "returncode": res.returncode,
            "timed_out": res.timed_out,
        }
