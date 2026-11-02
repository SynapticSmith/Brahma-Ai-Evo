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


def test_trusted_root_allows_outside_workspace_interactively_without_prompt(
    workspace: Path, tmp_path: Path
):
    trusted_dir = tmp_path.parent / f"{tmp_path.name}_trusted_code"
    trusted_dir.mkdir(exist_ok=True)
    target = trusted_dir / "project" / "notes.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hello\n", encoding="utf-8")

    policy = PermissionPolicy.from_dict(
        {"paths": {"trusted_roots": [str(trusted_dir)]}}
    )
    res = policy.evaluate(
        "read_file",
        {"path": str(target)},
        tool_class="read",
        context_workspace=workspace,
        headless=False,
    )
    assert res.decision == ALLOW
    assert res.rule == "classes.read"


def test_trusted_root_does_not_match_sibling_prefix_or_parent(
    workspace: Path, tmp_path: Path
):
    base = tmp_path.parent / f"{tmp_path.name}_roots"
    code = base / "code"
    code2 = base / "code2"
    nested_ws = code / "sub" / "repo"
    code.mkdir(parents=True, exist_ok=True)
    code2.mkdir(parents=True, exist_ok=True)
    nested_ws.mkdir(parents=True, exist_ok=True)

    sibling_file = code2 / "secret.txt"
    parent_file = base / "parent_secret.txt"

    policy = PermissionPolicy.from_dict(
        {"paths": {"trusted_roots": [str(code)]}}
    )
    # `code` must not match `code2`
    sibling_res = policy.evaluate(
        "read_file",
        {"path": str(sibling_file)},
        tool_class="read",
        context_workspace=workspace,
        headless=False,
    )
    assert sibling_res.decision == ASK
    assert sibling_res.rule == "paths.outside_workspace"

    # No upward inheritance: `base` above `code` is not trusted even when the
    # workspace itself is nested inside `code`.
    parent_res = policy.evaluate(
        "read_file",
        {"path": str(parent_file)},
        tool_class="read",
        context_workspace=nested_ws,
        headless=False,
    )
    assert parent_res.decision == ASK
    assert parent_res.rule == "paths.outside_workspace"


def test_trusted_root_rejects_symlink_that_resolves_outside_root(
    tmp_path: Path, monkeypatch
):
    ws = tmp_path / "workspace"
    trusted_dir = tmp_path / "trusted_root"
    outside_dir = tmp_path / "outside_secret"
    ws.mkdir()
    trusted_dir.mkdir()
    outside_dir.mkdir()
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("top secret\n", encoding="utf-8")

    link_path = trusted_dir / "escape_link.txt"
    created_symlink = False
    try:
        link_path.symlink_to(outside_file)
        created_symlink = True
    except OSError:
        created_symlink = False

    if not created_symlink:
        orig_resolve = Path.resolve

        def _fake_resolve(self: Path, strict: bool = False) -> Path:
            if str(self) == str(link_path):
                return orig_resolve(outside_file, strict=strict)
            return orig_resolve(self, strict=strict)

        monkeypatch.setattr(Path, "resolve", _fake_resolve)

    policy = PermissionPolicy.from_dict(
        {"paths": {"trusted_roots": [str(trusted_dir)]}}
    )
    res = policy.evaluate(
        "read_file",
        {"path": str(link_path)},
        tool_class="read",
        context_workspace=ws,
        headless=False,
    )
    assert res.decision == ASK
    assert res.rule == "paths.outside_workspace"


def test_protected_files_under_trusted_root_still_trigger_paths_protected(
    tmp_path: Path
):
    ws = tmp_path / "workspace"
    ws.mkdir()
    trusted_dir = tmp_path / "trusted_code"
    (trusted_dir / "anything").mkdir(parents=True)
    env_file = trusted_dir / "anything" / ".env"
    key_file = trusted_dir / "anything" / "server.key"

    policy = PermissionPolicy.from_dict(
        {"paths": {"trusted_roots": [str(trusted_dir)]}}
    )
    for secret in (env_file, key_file):
        res = policy.evaluate(
            "read_file",
            {"path": str(secret)},
            tool_class="read",
            context_workspace=ws,
            headless=False,
        )
        assert res.decision == ASK
        assert res.rule == "paths.protected"


def test_headless_mode_still_denies_outside_workspace_regardless_of_trusted_roots(
    tmp_path: Path
):
    ws = tmp_path / "workspace"
    ws.mkdir()
    trusted_dir = tmp_path / "trusted_code"
    trusted_dir.mkdir()
    target = trusted_dir / "file.txt"

    policy = PermissionPolicy.from_dict(
        {"paths": {"trusted_roots": [str(trusted_dir)]}}
    )
    res = policy.evaluate(
        "read_file",
        {"path": str(target)},
        tool_class="read",
        context_workspace=ws,
        headless=True,
    )
    assert res.decision == DENY
    assert res.rule == "headless.outside_workspace"


def test_unresolvable_or_relative_trusted_roots_are_ignored_with_diagnostics(
    tmp_path: Path
):
    ws = tmp_path / "workspace"
    ws.mkdir()
    missing_dir = tmp_path / "does_not_exist"
    file_not_dir = tmp_path / "plain.txt"
    file_not_dir.write_text("x", encoding="utf-8")

    policy = PermissionPolicy.from_dict(
        {
            "paths": {
                "trusted_roots": [
                    "relative/root",
                    str(missing_dir),
                    str(file_not_dir),
                ]
            }
        }
    )
    assert policy.trusted_roots == []
    assert len(policy.trusted_root_diagnostics) == 3
    described = policy.describe()
    assert "trusted_root_diagnostics" in described
    assert len(described["trusted_root_diagnostics"]) == 3

    res = policy.evaluate(
        "read_file",
        {"path": str(missing_dir / "a.txt")},
        tool_class="read",
        context_workspace=ws,
        headless=False,
    )
    assert res.decision == ASK
    assert res.rule == "paths.outside_workspace"


@pytest.mark.parametrize(
    "bad_roots",
    [
        "D:\\code",
        [""],
        [123],
    ],
)
def test_malformed_trusted_roots_fail_at_load(bad_roots):
    with pytest.raises(PolicyError):
        PermissionPolicy.from_dict({"paths": {"trusted_roots": bad_roots}})

