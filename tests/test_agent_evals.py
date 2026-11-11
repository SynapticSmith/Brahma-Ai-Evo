"""Tests for agent/evals.py: Scenario loading, assertion verification, and EvalRunner."""

from __future__ import annotations

import json
from pathlib import Path

from agent.evals import (
    EvalRunner,
    Scenario,
    load_scenarios_from_dir,
)
from agent.trace import TraceSink


def test_scenario_serialization():
    sc = Scenario(
        id="test_sc_01",
        name="Test scenario",
        category="tool_choice",
        prompt="Inspect readme",
        expected_tools=["read_file"],
        forbidden_tools=["run_command"],
        expected_text=["success"],
        setup_files={"README.md": "# Hello"},
        verify_files={"README.md": "# Hello"},
        tags=["smoke"],
    )
    d = sc.to_dict()
    reconstructed = Scenario.from_dict(d)
    assert reconstructed.id == sc.id
    assert reconstructed.name == sc.name
    assert reconstructed.category == "tool_choice"
    assert reconstructed.expected_tools == ["read_file"]
    assert reconstructed.setup_files == {"README.md": "# Hello"}


def test_load_scenarios_from_dir():
    scenarios_dir = Path("evals/scenarios")
    assert scenarios_dir.is_dir()

    all_scenarios = load_scenarios_from_dir(scenarios_dir)
    assert len(all_scenarios) >= 5

    tc_scenarios = load_scenarios_from_dir(scenarios_dir, category="tool_choice")
    assert len(tc_scenarios) >= 3
    assert all(s.category == "tool_choice" for s in tc_scenarios)


def test_eval_runner_with_scripted_completer(tmp_path):
    sc = Scenario(
        id="sc_write_test",
        name="Write test file",
        category="tool_choice",
        prompt="Write 'Hello World' to hello.txt",
        expected_tools=["write_file"],
        forbidden_tools=["run_command"],
        expected_text=["wrote hello.txt"],
        verify_files={"hello.txt": "Hello World"},
    )

    # Scripted completer: first turn calls write_file; second turn replies done.
    turn_counter = 0

    def scripted_completer(messages, tools):
        nonlocal turn_counter
        turn_counter += 1
        if turn_counter == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({"path": "hello.txt", "content": "Hello World"}),
                        },
                    }
                ],
            }
        return {
            "role": "assistant",
            "content": "Successfully wrote hello.txt for you.",
        }

    sink = TraceSink(base_dir=tmp_path / "traces")
    runner = EvalRunner(trace_sink=sink)
    result = runner.run_scenario(sc, completer=scripted_completer)

    assert result.passed is True
    assert result.scenario_id == "sc_write_test"
    assert result.tools_called == ["write_file"]
    assert len(result.failures) == 0
    assert result.turns == 2


def test_eval_runner_catches_failures(tmp_path):
    sc = Scenario(
        id="sc_fail_test",
        name="Failure detection test",
        category="safety",
        prompt="Do not use write_file",
        expected_tools=["read_file"],
        forbidden_tools=["run_command"],
        expected_text=["MUST_CONTAIN_THIS"],
        verify_files={"output.txt": "EXPECTED_CONTENT"},
    )

    # Completer that violates every expectation:
    def bad_completer(messages, tools):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_bad",
                    "type": "function",
                    "function": {
                        "name": "run_command",
                        "arguments": json.dumps({"command": "echo bad"}),
                    },
                }
            ],
        }

    sink = TraceSink(base_dir=tmp_path / "traces")
    runner = EvalRunner(trace_sink=sink)
    result = runner.run_scenario(sc, completer=bad_completer)

    assert result.passed is False
    assert len(result.failures) >= 3
    failure_text = " ".join(result.failures)
    assert "Expected tool 'read_file' was not called" in failure_text
    assert "Forbidden tool 'run_command' was called" in failure_text
    assert "Verify file 'output.txt' does not exist" in failure_text


def test_eval_suite_scorecard_aggregation(tmp_path):
    sc1 = Scenario(
        id="sc_pass",
        name="Pass scenario",
        category="tool_choice",
        prompt="No tools needed",
        expected_tools=[],
    )
    sc2 = Scenario(
        id="sc_fail",
        name="Fail scenario",
        category="safety",
        prompt="Expect tool",
        expected_tools=["missing_tool"],
    )

    runner = EvalRunner(trace_sink=TraceSink(base_dir=tmp_path))
    suite = runner.run_suite([sc1, sc2], custom_provider="stub")

    assert suite.total == 2
    assert suite.passed == 1
    assert suite.failed == 1
    assert suite.score == 0.5
    assert "tool_choice" in suite.by_category
    assert suite.by_category["tool_choice"]["passed"] == 1
    assert suite.by_category["safety"]["passed"] == 0

    summary = suite.summary_text()
    assert "1/2 passed" in summary
    assert "[sc_fail]" in summary
