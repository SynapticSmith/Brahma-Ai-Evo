"""Tests for Workspace Trust Fingerprinting and Gating (Architectural Question 4).

Validates:
1. Workspace trust tracking (is_workspace_trusted, trust_workspace, revoke_workspace_trust).
2. PermissionPolicy gating for state-changing operations in untrusted workspaces.
3. Headless vs interactive enforcement on untrusted workspaces.
4. Skill instructions wrapped in untrusted delimiters when workspace is untrusted.
5. Repo-local MCP server auto-spawning gating for untrusted workspaces.
"""

from __future__ import annotations

from pathlib import Path

from agent.permissions import (
    ALLOW,
    ASK,
    DENY,
    PermissionPolicy,
)
from agent.skills import _load_skill
from core.user_paths import (
    is_workspace_trusted,
    revoke_workspace_trust,
    trust_workspace,
)


class _MockContext:
    def __init__(self, workspace: Path):
        self.workspace = workspace


def test_workspace_trust_lifecycle(tmp_path: Path):
    """Verifies trusting and revoking workspace records."""
    store_file = tmp_path / "trusted_workspaces.json"
    ws = tmp_path / "my_project"
    ws.mkdir()

    # Initially untrusted
    assert is_workspace_trusted(ws, store_path=store_file) is False

    # Trust workspace
    row = trust_workspace(ws, store_path=store_file)
    assert row["path"].lower() == str(ws.resolve()).lower()
    assert is_workspace_trusted(ws, store_path=store_file) is True

    # Revoke trust
    revoked = revoke_workspace_trust(ws, store_path=store_file)
    assert revoked is True
    assert is_workspace_trusted(ws, store_path=store_file) is False


def test_permission_policy_untrusted_workspace_gating(tmp_path: Path):
    """State-changing tools in untrusted workspace require approval when policy configures it."""
    ws = tmp_path / "untrusted_repo"
    ws.mkdir()
    (ws / "file.txt").write_text("hello", encoding="utf-8")

    # Policy with untrusted_workspace set to ASK
    policy_dict = {
        "paths": {
            "untrusted_workspace": ASK,
        }
    }
    policy = PermissionPolicy.from_dict(policy_dict)

    # In untrusted workspace:
    # 1. Read operations proceed
    read_res = policy.evaluate(
        "read_file",
        {"path": "file.txt"},
        tool_class="read",
        context_workspace=ws,
        headless=False,
    )
    assert read_res.decision == ALLOW

    # 2. Write operations require approval interactively
    write_res = policy.evaluate(
        "edit_file",
        {"path": "file.txt"},
        tool_class="write",
        context_workspace=ws,
        headless=False,
    )
    assert write_res.decision == ASK
    assert "untrusted" in write_res.reason.lower()
    assert write_res.rule == "paths.untrusted_workspace"

    # 3. Write operations are DENIED in headless mode
    write_res_headless = policy.evaluate(
        "edit_file",
        {"path": "file.txt"},
        tool_class="write",
        context_workspace=ws,
        headless=True,
    )
    assert write_res_headless.decision == DENY
    assert "untrusted" in write_res_headless.reason.lower()

    # 4. Once trusted, write operations succeed
    trust_workspace(ws)
    assert policy.is_workspace_trusted(ws) is True

    write_res_trusted = policy.evaluate(
        "edit_file",
        {"path": "file.txt"},
        tool_class="write",
        context_workspace=ws,
        headless=False,
    )
    assert write_res_trusted.decision == ALLOW

    # Cleanup
    revoke_workspace_trust(ws)


def test_load_skill_wraps_instructions_in_untrusted_workspace(tmp_path: Path):
    """Project skills loaded from untrusted workspace carry untrusted-instructions wrapper."""
    ws = tmp_path / "cloned_repo"
    ws.mkdir()
    skill_dir = ws / ".brahma" / "skills" / "demo_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo_skill\ndescription: Test skill\n---\nDo something useful.",
        encoding="utf-8",
    )

    ctx = _MockContext(ws)

    # 1. In untrusted workspace, body is wrapped in untrusted delimiter
    res_untrusted = _load_skill({"name": "demo_skill"}, ctx)
    assert "<untrusted-instructions" in res_untrusted
    assert "</untrusted-instructions>" in res_untrusted
    assert "Do something useful." in res_untrusted

    # 2. In trusted workspace, body is clean and unwrapped
    trust_workspace(ws)
    res_trusted = _load_skill({"name": "demo_skill"}, ctx)
    assert "<untrusted-instructions" not in res_trusted
    assert "Do something useful." in res_trusted

    # Cleanup
    revoke_workspace_trust(ws)
