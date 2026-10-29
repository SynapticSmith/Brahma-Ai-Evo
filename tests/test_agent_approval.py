from __future__ import annotations

import threading
from pathlib import Path

from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from core import confirm


class Answers:
    """Approver that returns canned answers and records the calls."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, name, args, detail):
        self.calls.append((name, dict(args)))
        if not self.answers:
            return False
        return self.answers.pop(0)


def _runtime(tmp_path: Path, approver, policy=None) -> AgentRuntime:
    return AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict(policy or {"classes": {"read": "ask", "exec": "ask"}}),
        approver=approver,
        headless=False,
    )


# -- runtime session approvals --------------------------------------------- #


def test_always_allow_skips_the_prompt_for_the_same_tool(tmp_path: Path):
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    approver = Answers("always")
    runtime = _runtime(tmp_path, approver)
    events = []
    runtime.event_sink = events.append

    first = runtime.execute("read_file", {"path": "note.txt"})
    second = runtime.execute("read_file", {"path": "note.txt"})

    assert "hello" in first and "hello" in second
    assert len(approver.calls) == 1
    assert [e["decision"] for e in events if e.get("type") == "permission"] == [
        "ask",
        "always",
        "allow",
    ]
    assert [e["rule"] for e in events if e.get("type") == "permission"][-1] == "session.allow"


def test_a_plain_yes_is_not_remembered(tmp_path: Path):
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    approver = Answers(True, True)
    runtime = _runtime(tmp_path, approver)

    runtime.execute("read_file", {"path": "note.txt"})
    runtime.execute("read_file", {"path": "note.txt"})

    assert len(approver.calls) == 2
    assert runtime.session_allow == set()


def test_exec_approval_is_scoped_to_the_exact_command(tmp_path: Path):
    approver = Answers("always", "always")
    runtime = _runtime(tmp_path, approver)

    runtime.execute("run_command", {"command": "echo one"})
    runtime.execute("run_command", {"command": "echo one"})
    assert len(approver.calls) == 1

    runtime.execute("run_command", {"command": "echo two"})
    assert len(approver.calls) == 2


def test_deny_is_never_overridden_by_a_session_approval(tmp_path: Path):
    approver = Answers(True)
    runtime = _runtime(
        tmp_path,
        approver,
        policy={"classes": {"read": "allow"}, "paths": {"protected": "deny"}},
    )
    runtime.allow_for_session("read_file", {"path": ".env"})
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")

    output = runtime.execute("read_file", {"path": ".env"})

    assert output.startswith("Permission denied")
    assert approver.calls == []
    assert "SECRET" not in output


def test_session_approvals_survive_reconfigure(tmp_path: Path):
    from agent import runtime as runtime_module

    approver = Answers("always")
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    previous = runtime_module.current()
    try:
        runtime_module.configure(
            workspace=tmp_path,
            policy=PermissionPolicy.from_dict({"classes": {"read": "ask"}}),
            approver=approver,
            headless=False,
        )
        first = runtime_module.current().execute("read_file", {"path": "note.txt"})
        assert "hello" in first

        runtime_module.configure(workspace=tmp_path)
        second = runtime_module.current().execute("read_file", {"path": "note.txt"})
        assert "hello" in second
        assert len(approver.calls) == 1

        runtime_module.current().clear_session_approvals()
        assert runtime_module.current().session_allow == set()
    finally:
        runtime_module.configure(
            workspace=previous.workspace,
            policy=previous.policy,
            approver=previous.approver,
            headless=previous.headless,
        )


# -- confirm tri-state ------------------------------------------------------ #


class _Gate:
    def __init__(self):
        self.shown = threading.Event()
        self.hidden = 0
        self.title = ""

    def show(self, title, detail):
        self.title = title
        self.shown.set()

    def hide(self):
        self.hidden += 1


def _bind(gate):
    saved = (confirm._show_cb, confirm._hide_cb, confirm._log_cb, confirm._on_qt_main_thread)
    confirm.bind(gate.show, gate.hide, lambda _msg: None)
    confirm._on_qt_main_thread = lambda: False
    return saved


def _restore(saved):
    confirm._show_cb, confirm._hide_cb, confirm._log_cb, confirm._on_qt_main_thread = saved


def _ask_in_thread(key="run_command", detail="echo hi"):
    result = []
    worker = threading.Thread(
        target=lambda: result.append(confirm.ask_blocking_choice(key, detail)),
        daemon=True,
    )
    worker.start()
    return result, worker


def test_always_answer_is_reported_back_to_the_caller():
    gate = _Gate()
    saved = _bind(gate)
    try:
        result, worker = _ask_in_thread()
        assert gate.shown.wait(timeout=5)
        assert confirm.pending_allows_always() is True
        confirm.resolve(True, always=True)
        worker.join(timeout=5)
        assert result == ["always"]
    finally:
        _restore(saved)


def test_plain_confirm_is_reported_as_allow():
    gate = _Gate()
    saved = _bind(gate)
    try:
        result, worker = _ask_in_thread()
        assert gate.shown.wait(timeout=5)
        confirm.resolve(True)
        worker.join(timeout=5)
        assert result == ["allow"]
    finally:
        _restore(saved)


def test_cancel_is_reported_as_deny():
    gate = _Gate()
    saved = _bind(gate)
    try:
        result, worker = _ask_in_thread()
        assert gate.shown.wait(timeout=5)
        confirm.resolve(False)
        worker.join(timeout=5)
        assert result == ["deny"]
    finally:
        _restore(saved)


def test_always_allow_is_not_offered_for_irreversible_confirmations():
    saved = _bind(_Gate())
    try:
        message = confirm.request("shutdown", "Shut down?", "detail", lambda: "done")
        assert "[CONFIRMATION_PENDING]" in message
        assert confirm.pending_allows_always() is False
        confirm.resolve(False)
    finally:
        _restore(saved)


def test_expired_approval_is_denied():
    gate = _Gate()
    saved = _bind(gate)
    try:
        assert confirm.ask_blocking_choice("read_file", "detail", timeout=1) == "deny"
        assert gate.hidden == 1
    finally:
        _restore(saved)


def test_heading_and_remember_label_reach_the_gate():
    gate = _Gate()
    saved = _bind(gate)
    try:
        result = []
        worker = threading.Thread(
            target=lambda: result.append(
                confirm.ask_blocking_choice(
                    "run_command",
                    "rule: classes.exec\n\ncommand: pytest -q",
                    heading="Run this command?",
                    remember="this exact command (pytest -q)",
                )
            ),
            daemon=True,
        )
        worker.start()
        assert gate.shown.wait(timeout=5)
        assert gate.title == "Run this command?"
        assert confirm.pending_remember_label() == "this exact command (pytest -q)"
        confirm.resolve(True)
        worker.join(timeout=5)
        assert result == ["allow"]
    finally:
        _restore(saved)


# -- CLI TTY ---------------------------------------------------------------- #


def test_tty_approver_supports_always(monkeypatch):
    from agent import __main__ as cli

    class _TTY:
        def isatty(self):
            return True

    monkeypatch.setattr(cli.sys, "stdin", _TTY())
    monkeypatch.setattr("builtins.input", lambda: "a")

    assert cli._tty_approver("run_command", {"command": "echo hi"}, "detail") == "always"
