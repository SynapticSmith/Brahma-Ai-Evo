"""Unit and regression tests for Auto-Heal Engine & Learned Rules (ADR-018 / User Note 7)."""

from __future__ import annotations

import json

from actions.auto_heal_engine import (
    SafetySandbox,
    TracebackAnalyzer,
    auto_heal,
)
from core.learned_rules import LearnedRulesEngine


def test_traceback_analyzer(tmp_path):
    action_py = tmp_path / "sample_action.py"
    action_py.write_text("def sample(): pass\n", encoding="utf-8")

    sample_tb = f"""
Traceback (most recent call last):
  File "C:\\Users\\Admin\\Brahma-Ai-Evo\\main.py", line 120, in run
    do_something()
  File "{action_py}", line 42, in sample_action
    result = 10 / 0
ZeroDivisionError: division by zero
"""
    parsed = TracebackAnalyzer.parse(sample_tb)
    assert parsed.get("exception_type") == "ZeroDivisionError"
    assert "division by zero" in parsed.get("exception_message", "")
    assert "sample_action.py" in parsed.get("target_file", "")
    assert parsed.get("line_number") == 42


def test_safety_sandbox_ast_validation():
    valid_code = "def add(a, b):\n    return a + b\n"
    ok, err = SafetySandbox.validate_code(valid_code)
    assert ok is True
    assert err is None

    invalid_code = "def add(a, b):\n    return a + \n"
    ok, err = SafetySandbox.validate_code(invalid_code)
    assert ok is False
    assert "Syntax Error" in str(err)


def test_safety_sandbox_backup_and_rollback(tmp_path, monkeypatch):
    backups = tmp_path / "backups"
    history = tmp_path / "history.json"
    backups.mkdir()

    monkeypatch.setattr("actions.auto_heal_engine.BACKUPS_DIR", backups)
    monkeypatch.setattr("actions.auto_heal_engine.PATCH_HISTORY_FILE", history)

    # Create target file
    target_py = tmp_path / "worker.py"
    target_py.write_text("x = 10 / 0\n", encoding="utf-8")

    # Create backup
    backup_path = SafetySandbox.create_backup(target_py)
    assert backup_path.exists()
    assert backup_path.read_text(encoding="utf-8") == "x = 10 / 0\n"

    # Simulate patch write
    target_py.write_text("x = 10 / 1\n", encoding="utf-8")

    # Record history
    history.write_text(json.dumps([{
        "patch_id": "patch-001",
        "target_file": str(target_py),
        "backup_path": str(backup_path),
        "status": "applied",
        "explanation": "Fix zero division",
        "line_number": 1,
    }]), encoding="utf-8")

    # Test auto_heal history reporting
    hist_text = auto_heal({"action": "history"})
    assert "patch-001" in hist_text

    # Test rollback
    rb_res = SafetySandbox.rollback_patch("patch-001")
    assert rb_res.get("success") is True
    assert target_py.read_text(encoding="utf-8") == "x = 10 / 0\n"


def test_learned_rules_engine(tmp_path, monkeypatch):
    rules_file = tmp_path / "config" / "learned_rules.json"
    monkeypatch.setattr("core.learned_rules.CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr("core.learned_rules.RULES_FILE", rules_file)

    # 1. Add rule
    res1 = LearnedRulesEngine.add_rule("Always use Chrome browser for searches", category="browser")
    assert res1["success"] is True
    rule_id = res1["rule"]["id"]

    # 2. Deduplication
    res2 = LearnedRulesEngine.add_rule("Always use Chrome browser for searches")
    assert res2["success"] is True
    assert "already exists" in res2["message"]

    # 3. List rules
    rules = LearnedRulesEngine.list_rules(active_only=True)
    assert len(rules) == 1
    assert rules[0]["id"] == rule_id

    # 4. Prompt injections
    prompt = LearnedRulesEngine.get_prompt_injections()
    assert "USER PREFERENCES & LEARNED BEHAVIORAL DIRECTIVES:" in prompt
    assert "Always use Chrome browser for searches" in prompt

    # 5. Toggle rule
    toggle_res = LearnedRulesEngine.toggle_rule(rule_id)
    assert toggle_res["success"] is True
    assert "deactivated" in toggle_res["message"]
    assert len(LearnedRulesEngine.list_rules(active_only=True)) == 0

    # 6. Delete rule
    del_res = LearnedRulesEngine.delete_rule(rule_id)
    assert del_res["success"] is True
    assert len(LearnedRulesEngine.list_rules(active_only=False)) == 0
