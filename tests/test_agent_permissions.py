"""Permission policy: defaults, overrides, path scoping, headless behaviour."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.permissions import (
    ALLOW,
    ASK,
    DENY,
    PermissionPolicy,
    PolicyError,
    default_policy_dict,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    return tmp_path


def test_defaults_allow_reads_and_ask_for_exec_in_an_interactive_session(workspace: Path):
    policy = PermissionPolicy.from_dict({})
    assert policy.evaluate("read_file", {"path": "pkg/app.py"}, tool_class="read",
                           context_workspace=workspace, headless=False).decision == ALLOW
    result = policy.evaluate("run_command", {"command": "pytest"}, tool_class="exec",
                             context_workspace=workspace, headless=False)
    assert result.decision == ASK
    assert result.rule == "classes.exec"


def test_headless_turns_exec_ask_into_deny(workspace: Path):
    policy = PermissionPolicy.from_dict({})
    result = policy.evaluate("run_command", {"command": "pytest"}, tool_class="exec",
                             context_workspace=workspace, headless=True)
    assert result.decision == DENY
    assert result.rule == "headless.exec"


def test_outside_workspace_is_asked_then_denied_when_headless(workspace: Path, tmp_path: Path):
    outside = tmp_path.parent / "elsewhere.txt"
    policy = PermissionPolicy.from_dict({})
    interactive = policy.evaluate("read_file", {"path": str(outside)}, tool_class="read",
                                  context_workspace=workspace, headless=False)
    assert interactive.decision == ASK
    assert "outside the workspace" in interactive.reason

    headless = policy.evaluate("read_file", {"path": str(outside)}, tool_class="read",
                               context_workspace=workspace, headless=True)
    assert headless.decision == DENY
    assert headless.rule == "headless.outside_workspace"


def test_relative_escape_is_caught(workspace: Path):
    policy = PermissionPolicy.from_dict({})
    result = policy.evaluate("read_file", {"path": "../../secrets.txt"}, tool_class="read",
                             context_workspace=workspace, headless=True)
    assert result.decision == DENY


def test_protected_files_are_gated_even_interactively(workspace: Path):
    policy = PermissionPolicy.from_dict({})
    result = policy.evaluate("read_file", {"path": ".env"}, tool_class="read",
                             context_workspace=workspace, headless=False)
    assert result.decision == ASK
    assert result.rule == "paths.protected"


def test_catastrophic_command_is_hard_denied(workspace: Path):
    policy = PermissionPolicy.from_dict({})
    result = policy.evaluate("run_command", {"command": "rm -rf /"}, tool_class="exec",
                             context_workspace=workspace, headless=False)
    assert result.decision == DENY
    assert result.rule.startswith("exec.deny_patterns")


def test_explicit_tool_rule_beats_class_default(workspace: Path):
    policy = PermissionPolicy.from_dict({"tools": {"run_command": "deny"}})
    result = policy.evaluate("run_command", {"command": "echo hi"}, tool_class="exec",
                             context_workspace=workspace, headless=False)
    assert result.decision == DENY
    assert result.rule == "tools.run_command"


def test_requires_approval_is_denied_without_an_approver(workspace: Path):
    policy = PermissionPolicy.from_dict({})
    result = policy.evaluate("write_file", {"path": "x.txt"}, tool_class="write",
                             context_workspace=workspace, headless=True, requires_approval=True)
    assert result.decision == DENY
    assert result.rule == "tool.requires_approval"


def test_for_model_refusal_is_instructional(workspace: Path):
    policy = PermissionPolicy.from_dict({})
    result = policy.evaluate("run_command", {"command": "pytest"}, tool_class="exec",
                             context_workspace=workspace, headless=True)
    text = result.for_model("run_command")
    assert "Permission denied" in text
    assert "Do not retry" in text


def test_invalid_decision_fails_at_load(tmp_path: Path):
    path = tmp_path / "agent_permissions.json"
    path.write_text(json.dumps({"classes": {"exec": "maybe"}}), encoding="utf-8")
    with pytest.raises(PolicyError):
        PermissionPolicy.load(path)


def test_load_merges_over_defaults(tmp_path: Path):
    path = tmp_path / "agent_permissions.json"
    path.write_text(json.dumps({"classes": {"exec": "allow"}}), encoding="utf-8")
    policy = PermissionPolicy.load(path)
    assert policy.data["classes"]["exec"] == ALLOW
    assert policy.data["classes"]["read"] == ALLOW  # untouched default
    assert policy.source == path


def test_default_policy_is_not_mutable_through_the_constant():
    first = default_policy_dict()
    first["classes"]["exec"] = "allow"
    assert default_policy_dict()["classes"]["exec"] == ASK


def test_user_data_discovery_uses_the_config_subdirectory(tmp_path, monkeypatch):
    import core.user_paths as user_paths

    monkeypatch.setattr(user_paths, "get_user_data_dir", lambda: tmp_path)
    target = tmp_path / "config" / "agent_permissions.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"classes": {"exec": "allow"}}), encoding="utf-8")

    policy = PermissionPolicy.load()
    assert policy.source == target
    assert policy.data["classes"]["exec"] == ALLOW
