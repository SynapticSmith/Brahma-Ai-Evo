"""Policy hot-reload: an edited rule lands without restarting Brahma."""
from __future__ import annotations

import json
import os
from pathlib import Path

from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _runtime(tmp_path: Path, data: dict, **kwargs) -> tuple[AgentRuntime, Path]:
    policy_file = tmp_path / "agent_permissions.json"
    _write(policy_file, data)
    runtime = AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.load(policy_file),
        **{"headless": True, **kwargs},
    )
    return runtime, policy_file


def _bump(path: Path) -> None:
    """Make the mtime unmistakably different, without sleeping."""
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 5))


def test_an_edited_policy_takes_effect_without_a_restart(tmp_path: Path):
    (tmp_path / "note.txt").write_text("secret", encoding="utf-8")
    runtime, policy_file = _runtime(tmp_path, {"classes": {"read": "deny"}})

    assert "Permission denied" in runtime.execute("read_file", {"path": "note.txt"})

    _write(policy_file, {"classes": {"read": "allow"}})
    _bump(policy_file)
    reloaded = runtime.reload_policy(force=True)

    assert reloaded is not None
    assert "secret" in runtime.execute("read_file", {"path": "note.txt"})


def test_execute_checks_the_file_so_an_edit_lands_mid_session(tmp_path: Path):
    (tmp_path / "note.txt").write_text("secret", encoding="utf-8")
    runtime, policy_file = _runtime(
        tmp_path, {"classes": {"read": "deny"}}, policy_check_interval=0
    )
    assert "Permission denied" in runtime.execute("read_file", {"path": "note.txt"})

    _write(policy_file, {"classes": {"read": "allow"}})
    _bump(policy_file)

    assert "secret" in runtime.execute("read_file", {"path": "note.txt"})


def test_a_malformed_edit_keeps_the_last_good_policy(tmp_path: Path):
    (tmp_path / "note.txt").write_text("secret", encoding="utf-8")
    events = []
    runtime, policy_file = _runtime(
        tmp_path,
        {"classes": {"read": "deny"}},
        policy_check_interval=0,
        event_sink=events.append,
    )

    policy_file.write_text("{ this is not json", encoding="utf-8")
    _bump(policy_file)

    assert "Permission denied" in runtime.execute("read_file", {"path": "note.txt"})
    bad = [e for e in events if e.get("type") == "policy_reload"]
    assert bad and bad[-1]["ok"] is False
    assert "error" in bad[-1]
    # The old rules are still the ones in force.
    assert runtime.policy.evaluate("read_file", {"path": "note.txt"}, tool_class="read").decision == "deny"


def test_a_bad_decision_name_is_also_survivable(tmp_path: Path):
    runtime, policy_file = _runtime(tmp_path, {"classes": {"read": "allow"}})
    _write(policy_file, {"classes": {"read": "sometimes"}})
    _bump(policy_file)

    assert runtime.reload_policy(force=True) is None
    assert runtime.policy.evaluate("read_file", {"path": "x"}, tool_class="read").decision == "allow"


def test_defaults_with_no_file_are_never_reloaded(tmp_path: Path):
    events = []
    runtime = AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict({"classes": {"read": "allow"}}),
        event_sink=events.append,
    )
    assert runtime.reload_policy(force=True) is None
    assert not [e for e in events if e.get("type") == "policy_reload"]


def test_the_throttle_skips_the_check_and_force_bypasses_it(tmp_path: Path):
    runtime, policy_file = _runtime(
        tmp_path,
        {"classes": {"read": "allow"}},
        policy_check_interval=60.0,
    )
    # Prime the interval: the first call is always allowed to look.
    assert runtime.reload_policy() is None

    _write(policy_file, {"classes": {"read": "deny"}})
    _bump(policy_file)

    # Inside the interval: the edit is not even stat'ed yet.
    assert runtime.reload_policy() is None
    assert runtime.policy.evaluate("read_file", {"path": "x"}, tool_class="read").decision == "allow"

    assert runtime.reload_policy(force=True) is not None
    assert runtime.policy.evaluate("read_file", {"path": "x"}, tool_class="read").decision == "deny"


def test_the_successful_reload_event_names_the_file(tmp_path: Path):
    events = []
    runtime, policy_file = _runtime(
        tmp_path, {"classes": {"read": "allow"}}, event_sink=events.append
    )
    _write(policy_file, {"classes": {"read": "ask"}})
    _bump(policy_file)

    assert runtime.reload_policy(force=True) is not None
    reloads = [e for e in events if e.get("type") == "policy_reload"]
    assert reloads == [{"type": "policy_reload", "ok": True, "source": str(policy_file)}]


def test_a_session_approval_does_not_survive_into_a_deny(tmp_path: Path):
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "b.txt").write_text("beta", encoding="utf-8")

    answers = iter(["always"])

    def approver(name, args, detail):
        return next(answers, False)

    runtime, policy_file = _runtime(
        tmp_path,
        {"classes": {"read": "ask"}},
        approver=approver,
        headless=False,
        policy_check_interval=0,
    )
    assert "alpha" in runtime.execute("read_file", {"path": "a.txt"})
    assert "beta" in runtime.execute("read_file", {"path": "b.txt"})  # remembered

    _write(policy_file, {"classes": {"read": "deny"}})
    _bump(policy_file)

    # A remembered human "yes" never widens a policy deny.
    assert "Permission denied" in runtime.execute("read_file", {"path": "b.txt"})
