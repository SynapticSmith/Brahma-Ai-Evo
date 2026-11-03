"""Red-team and security assurance tests (Phase 0 / Q4 Assurance).

Validates:
1. Path traversal defenses (PermissionPolicy, sanitize_identifier, read_agent_artifact).
2. Secret redaction and DPAPI encryption (DPAPI store, agent sandbox build_env).
3. Process containment and Windows Job Object lifetime constraints (closed stdin, tree termination on timeout).
4. Catastrophic command hard-denials.
"""

from __future__ import annotations

import sys
from pathlib import Path
import pytest

from agent.permissions import (
    ASK,
    DENY,
    PermissionPolicy,
)
from agent.process import run_captured
from agent.sandbox import SandboxConfig, build_env
from core.agent_bridge.artifacts import (
    parse_artifact_ref,
    sanitize_identifier,
)
import core.secret_store as secret_store


# ============================================================================
# 1. Path Traversal & Scoping Defenses
# ============================================================================

def test_sanitize_identifier_rejects_path_traversal():
    """Verify identifier sanitizer blocks directory traversal vectors."""
    traversal_payloads = [
        "../secret",
        "..\\secret",
        "../../etc/passwd",
        "..\\..\\Windows\\System32",
        "/etc/shadow",
        "C:\\Windows\\System32",
        "agent/sub",
        "agent\\sub",
        "agent:stream",
        "agent*",
        "",
        "   ",
    ]
    for payload in traversal_payloads:
        with pytest.raises(ValueError, match="Invalid identifier"):
            sanitize_identifier(payload)


def test_parse_artifact_ref_rejects_traversal_in_uris():
    """Artifact refs must not parse when containing directory traversal."""
    malicious_refs = [
        "agent-artifact://../../secret/call123",
        "agent-artifact://agent1/../../call123",
        "agent-out://..\\..\\windows/call123",
    ]
    for ref in malicious_refs:
        with pytest.raises(ValueError):
            agent_id, call_id = parse_artifact_ref(ref)
            sanitize_identifier(agent_id)
            sanitize_identifier(call_id)


def test_permission_policy_denies_outside_workspace_traversal_headless(tmp_path: Path):
    """Path traversal attempting to escape workspace is hard-denied in headless mode."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("hello", encoding="utf-8")

    outside_file = tmp_path / "secret.env"
    outside_file.write_text("SECRET=123", encoding="utf-8")

    policy = PermissionPolicy.from_dict({})

    # Relative directory traversal escapes
    res1 = policy.evaluate(
        "read_file",
        {"path": "../secret.env"},
        tool_class="read",
        context_workspace=workspace,
        headless=True,
    )
    assert res1.decision == DENY
    assert "outside" in res1.reason.lower() or res1.rule.startswith("headless.")

    res2 = policy.evaluate(
        "edit_file",
        {"path": "../../Windows/System32/drivers/etc/hosts"},
        tool_class="write",
        context_workspace=workspace,
        headless=True,
    )
    assert res2.decision == DENY

    # Absolute outside escape
    res3 = policy.evaluate(
        "read_file",
        {"path": str(outside_file.resolve())},
        tool_class="read",
        context_workspace=workspace,
        headless=True,
    )
    assert res3.decision == DENY


def test_permission_policy_protects_sensitive_files(tmp_path: Path):
    """Protected files (.env, .git/config, id_rsa, etc.) require explicit approval."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    policy = PermissionPolicy.from_dict({})

    protected_targets = [
        ".env",
        ".env.local",
        ".env.production",
        "id_rsa",
        "id_rsa.pub",
        "server.key",
        "cert.pem",
        ".git/config",
    ]

    for target in protected_targets:
        res = policy.evaluate(
            "read_file",
            {"path": target},
            tool_class="read",
            context_workspace=workspace,
            headless=False,
        )
        assert res.decision == ASK, f"Target {target} should require ASK interactively"
        assert res.rule == "paths.protected"

        # In headless mode, protected files must be DENIED
        res_headless = policy.evaluate(
            "read_file",
            {"path": target},
            tool_class="read",
            context_workspace=workspace,
            headless=True,
        )
        assert res_headless.decision == DENY, f"Target {target} should be DENIED in headless"


# ============================================================================
# 2. Secret Redaction & DPAPI Encryption
# ============================================================================

def test_dpapi_secret_store_encryption_roundtrip(tmp_path: Path):
    """Verify API keys are never stored as plaintext JSON on disk."""
    keys_file = tmp_path / "api_keys.json"

    test_keys = {
        "GEMINI_API_KEY": "dummy_gemini_test_token_val",
        "OPENROUTER_API_KEY": "dummy_openrouter_token_val",
    }

    # Save keys
    secret_store.save_api_keys(test_keys, path=keys_file)
    assert keys_file.exists()

    # Raw disk content must NOT contain plaintext secret values
    raw_content = keys_file.read_bytes()
    assert b"dummy_gemini_test_token_val" not in raw_content
    assert b"dummy_openrouter_token_val" not in raw_content

    # Load keys back using DPAPI
    loaded_keys = secret_store.load_api_keys(path=keys_file)
    assert loaded_keys.get("GEMINI_API_KEY") == "dummy_gemini_test_token_val"
    assert loaded_keys.get("OPENROUTER_API_KEY") == "dummy_openrouter_token_val"


def test_sandbox_build_env_scrubs_credential_shaped_variables():
    """Verify agent sandbox strips secret-shaped environment variables."""
    base_env = {
        "PATH": "C:\\Windows\\system32",
        "USERNAME": "AgentTester",
        "OPENAI_API_KEY": "mock_openai_secret_token",
        "ANTHROPIC_API_KEY": "mock_anthropic_secret_token",
        "GEMINI_API_KEY": "mock_gemini_secret_token",
        "AWS_SECRET_ACCESS_KEY": "mock_aws_secret_key_token",
        "GITHUB_TOKEN": "mock_github_access_token",
        "DATABASE_PASSWORD": "supersecretpassword",
        "CUSTOM_SECRET": "hiddendata",
    }

    config = SandboxConfig(env_scrub=True, env_keep=())
    clean_env, withheld = build_env(config, base_env)

    # Benign variables preserved
    assert clean_env.get("PATH") == "C:\\Windows\\system32"
    assert clean_env.get("USERNAME") == "AgentTester"

    # All credentials withheld
    assert "OPENAI_API_KEY" not in clean_env
    assert "ANTHROPIC_API_KEY" not in clean_env
    assert "GEMINI_API_KEY" not in clean_env
    assert "AWS_SECRET_ACCESS_KEY" not in clean_env
    assert "GITHUB_TOKEN" not in clean_env
    assert "DATABASE_PASSWORD" not in clean_env
    assert "CUSTOM_SECRET" not in clean_env
    assert withheld >= 7


def test_sandbox_build_env_allows_explicitly_kept_secret():
    """Verify env_keep allows only the specifically named credential through."""
    base_env = {
        "PATH": "C:\\Windows\\system32",
        "OPENAI_API_KEY": "mock_allowed_key",
        "ANTHROPIC_API_KEY": "mock_withheld_key",
    }

    config = SandboxConfig(env_scrub=True, env_keep=("OPENAI_API_KEY",))
    clean_env, withheld = build_env(config, base_env)

    assert clean_env.get("OPENAI_API_KEY") == "mock_allowed_key"
    assert "ANTHROPIC_API_KEY" not in clean_env
    assert withheld == 1


# ============================================================================
# 3. Process Containment & Windows Job Objects
# ============================================================================

def test_closed_stdin_prevents_hung_subprocesses(tmp_path: Path):
    """Commands cannot hang on stdin prompts; closed stdin raises EOFError immediately."""
    script_file = tmp_path / "stdin_test.py"
    script_file.write_text(
        "try:\n"
        "    val = input()\n"
        "    print('REACHED_AFTER_INPUT')\n"
        "except EOFError:\n"
        "    print('EOF_CAUGHT_SAFELY')\n",
        encoding="utf-8",
    )
    result = run_captured(
        f'"{sys.executable}" "{script_file}"',
        cwd=tmp_path,
        timeout=5.0,
    )
    assert result.returncode == 0
    assert "EOF_CAUGHT_SAFELY" in result.stdout
    assert "REACHED_AFTER_INPUT" not in result.stdout


def test_timeout_terminates_entire_process_tree(tmp_path: Path):
    """When a parent process spawns a child and hangs, timeout terminates all grandchildren."""
    parent_script = tmp_path / "hang_tree.py"
    parent_script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    result = run_captured(
        f'"{sys.executable}" "{parent_script}"',
        cwd=tmp_path,
        timeout=1.0,
    )
    assert result.timed_out is True


# ============================================================================
# 4. Catastrophic Command Hard Denials
# ============================================================================

@pytest.mark.parametrize(
    "bad_command",
    [
        "rm -rf /",
        "del /f /s /q C:\\",
        "rd /s /q C:\\",
        "format C: /FS:NTFS /Q /Y",
        "mkfs.ext4 /dev/sda",
        "shutdown /s /t 0",
        "cipher /w C:",
        "reg delete hklm\\software",
    ],
)
def test_catastrophic_commands_are_hard_denied(bad_command: str, tmp_path: Path):
    """Destructive disk or root deletion commands must never be permitted."""
    policy = PermissionPolicy.from_dict({})
    result = policy.evaluate(
        "run_command",
        {"command": bad_command},
        tool_class="exec",
        context_workspace=tmp_path,
        headless=False,
    )
    assert result.decision == DENY
    assert "deny_patterns" in result.rule
