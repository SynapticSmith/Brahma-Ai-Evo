"""Segment-aware parallel dispatch, ordering, and protocol-safe cancellation.

The rules under test:

* consecutive runs of policy-allowed, non-repeated `read` calls share a pool;
* a write, `exec`, native call, denied call or approval prompt is a fence:
  earlier calls finish first, later calls wait, so reads cannot observe a
  half-written file;
* a repeated call is skipped without a worker;
* results are appended in model order regardless of completion order;
* a cancelled turn still writes one `tool` message per emitted `tool_call`.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from agent.loop import CANCELLED_TOOL_OUTPUT, STOP_CANCELLED, STOP_COMPLETED, AgentLoop
from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime


def _runtime(tmp_path: Path, *, classes=None, headless=True, approver=None) -> AgentRuntime:
    data = {
        "classes": classes
        or {"read": "allow", "write": "allow", "exec": "allow", "native": "allow"},
        "paths": {"outside_workspace": "deny", "protected": "allow"},
    }
    return AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict(data),
        approver=approver,
        headless=headless,
    )


def _turn(*calls) -> dict:
    """One assistant message with several tool calls."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            for call_id, name, args in calls
        ],
    }


def _read(call_id: str, name: str = "read_file") -> tuple:
    return (call_id, name, {"path": f"{call_id}.txt"})


class Script:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages, tools):
        self.calls.append([dict(m) for m in messages])
        if not self.responses:
            return {"role": "assistant", "content": "done"}
        return self.responses.pop(0)


class Tracker:
    """Wraps ``runtime.execute`` to observe overlap and dispatch order.

    The delay makes overlap deterministic enough to assert: without it a file
    read can finish before the next worker starts, and the peak would be 1 even
    when the executor really is parallel.
    """

    def __init__(self, runtime: AgentRuntime, delay: float = 0.12, on_call=None):
        self.runtime = runtime
        self.delay = delay
        self.on_call = on_call
        self.active = 0
        self.peak = 0
        self.started = []
        self._lock = threading.Lock()
        original = runtime.execute

        def execute(name, args=None, **kwargs):
            with self._lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
                self.started.append(name)
            try:
                if self.delay:
                    time.sleep(self.delay)
                result = original(name, args, **kwargs)
                if self.on_call is not None:
                    self.on_call(name, args)
                return result
            finally:
                with self._lock:
                    self.active -= 1

        runtime.execute = execute  # type: ignore[method-assign]


def _prepare(tmp_path: Path, *names: str) -> None:
    for name in names:
        (tmp_path / name).write_text(f"content of {name}", encoding="utf-8")


def _tool_ids(result) -> list[str]:
    return [m["tool_call_id"] for m in result.messages if m.get("role") == "tool"]


def test_reads_in_one_turn_run_concurrently(tmp_path: Path):
    _prepare(tmp_path, "c1.txt", "c2.txt", "c3.txt")
    runtime = _runtime(tmp_path)
    tracker = Tracker(runtime)
    script = Script([
        _turn(_read("c1"), _read("c2"), _read("c3")),
        {"role": "assistant", "content": "ok"},
    ])

    result = AgentLoop(script, runtime=runtime, max_turns=4).run("read three files")

    assert result.stop_reason == STOP_COMPLETED
    assert tracker.peak >= 2, "expected overlapping read calls"
    assert sorted(tracker.started) == ["read_file"] * 3
    assert _tool_ids(result) == ["c1", "c2", "c3"]


def test_parallel_cap_of_one_serialises_the_turn(tmp_path: Path):
    _prepare(tmp_path, "c1.txt", "c2.txt", "c3.txt")
    runtime = _runtime(tmp_path)
    tracker = Tracker(runtime)
    script = Script([
        _turn(_read("c1"), _read("c2"), _read("c3")),
        {"role": "assistant", "content": "ok"},
    ])

    result = AgentLoop(script, runtime=runtime, max_turns=4, max_parallel_tools=1).run("go")

    assert tracker.peak == 1
    assert _tool_ids(result) == ["c1", "c2", "c3"]


def test_reads_before_and_after_a_write_overlap_in_their_own_segments(tmp_path: Path):
    _prepare(tmp_path, "c1.txt", "c2.txt", "c4.txt", "c5.txt")
    runtime = _runtime(tmp_path)
    tracker = Tracker(runtime)
    script = Script([
        _turn(
            _read("c1"),
            _read("c2"),
            ("c3", "write_file", {"path": "out.txt", "content": "hi"}),
            _read("c4"),
            _read("c5"),
        ),
        {"role": "assistant", "content": "ok"},
    ])

    result = AgentLoop(script, runtime=runtime, max_turns=4).run("go")

    assert tracker.peak >= 2, "each read burst should overlap"
    # The write is a fence: both reads before it finished, both after wait.
    assert set(tracker.started[:2]) == {"read_file"}
    assert tracker.started[2] == "write_file"
    assert tracker.started[3:] == ["read_file", "read_file"]
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hi"
    assert _tool_ids(result) == ["c1", "c2", "c3", "c4", "c5"]


def test_a_leading_write_runs_before_the_following_read_burst(tmp_path: Path):
    _prepare(tmp_path, "c2.txt", "c3.txt")
    runtime = _runtime(tmp_path)
    tracker = Tracker(runtime)
    script = Script([
        _turn(
            ("c1", "write_file", {"path": "out.txt", "content": "hi"}),
            _read("c2"),
            _read("c3"),
        ),
        {"role": "assistant", "content": "ok"},
    ])

    result = AgentLoop(script, runtime=runtime, max_turns=4).run("go")

    assert tracker.started[0] == "write_file"
    assert tracker.peak >= 2
    assert _tool_ids(result) == ["c1", "c2", "c3"]


def test_a_read_that_needs_approval_serialises_the_turn(tmp_path: Path):
    _prepare(tmp_path, "c1.txt", "c2.txt")
    answers = []

    def approver(name, args, detail):
        answers.append(name)
        return True

    runtime = _runtime(
        tmp_path,
        classes={"read": "ask", "write": "allow"},
        headless=False,
        approver=approver,
    )
    tracker = Tracker(runtime)
    script = Script([
        _turn(_read("c1"), _read("c2")),
        {"role": "assistant", "content": "ok"},
    ])

    AgentLoop(script, runtime=runtime, max_turns=4).run("go")

    assert tracker.peak == 1
    assert answers == ["read_file", "read_file"]


def test_an_approval_prompt_is_a_fence_but_later_reads_still_overlap(tmp_path: Path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.txt").write_text("a", encoding="utf-8")
    (work / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")

    answers = []

    def approver(name, args, detail):
        answers.append((name, dict(args)))
        return True

    runtime = AgentRuntime(
        workspace=work,
        policy=PermissionPolicy.from_dict(
            {
                "classes": {"read": "allow", "write": "allow"},
                "paths": {"outside_workspace": "ask", "protected": "allow"},
            }
        ),
        approver=approver,
        headless=False,
    )
    tracker = Tracker(runtime)
    script = Script([
        _turn(
            ("c1", "read_file", {"path": "a.txt"}),
            ("c2", "read_file", {"path": "../outside.txt"}),
            ("c3", "read_file", {"path": "b.txt"}),
            ("c4", "read_file", {"path": "a.txt"}),
        ),
        {"role": "assistant", "content": "ok"},
    ])

    result = AgentLoop(script, runtime=runtime, max_turns=4).run("go")

    # Exactly the outside read asked; it is a barrier, not a turn-wide veto.
    assert len(answers) == 1
    assert answers[0][1]["path"].endswith("outside.txt")
    assert tracker.peak >= 2
    assert _tool_ids(result) == ["c1", "c2", "c3", "c4"]
    contents = {
        m["tool_call_id"]: m["content"]
        for m in result.messages
        if m.get("role") == "tool"
    }
    assert "outside" in contents["c2"]


def test_a_repeated_call_is_skipped_without_a_worker(tmp_path: Path):
    _prepare(tmp_path, "c1.txt", "c2.txt")
    runtime = _runtime(tmp_path)
    tracker = Tracker(runtime)
    script = Script([
        _turn(
            ("c1", "read_file", {"path": "c1.txt"}),
            ("c1-repeat", "read_file", {"path": "c1.txt"}),
            ("c2", "read_file", {"path": "c2.txt"}),
        ),
        {"role": "assistant", "content": "ok"},
    ])

    result = AgentLoop(script, runtime=runtime, max_turns=4, repeat_limit=1).run("go")

    assert tracker.started == ["read_file", "read_file"]
    # A skipped call has no side effect, so it must not split the burst.
    assert tracker.peak >= 2
    contents = {
        m["tool_call_id"]: m["content"]
        for m in result.messages
        if m.get("role") == "tool"
    }
    assert contents["c1-repeat"].startswith("Identical call skipped")
    assert "content of c2.txt" in contents["c2"]
    assert _tool_ids(result) == ["c1", "c1-repeat", "c2"]


def test_results_keep_model_order_even_when_completion_order_differs(tmp_path: Path):
    _prepare(tmp_path, "c1.txt", "c2.txt", "c3.txt")
    runtime = _runtime(tmp_path)
    original = runtime.execute

    def execute(name, args=None, **kwargs):
        # Make the first call the slowest: a naive `as_completed` gather would
        # then write c3, c2, c1 into the transcript.
        time.sleep({"c1.txt": 0.20, "c2.txt": 0.10}.get(str((args or {}).get("path")), 0))
        return original(name, args, **kwargs)

    runtime.execute = execute  # type: ignore[method-assign]
    script = Script([
        _turn(_read("c1"), _read("c2"), _read("c3")),
        {"role": "assistant", "content": "ok"},
    ])

    result = AgentLoop(script, runtime=runtime, max_turns=4).run("go")

    assert _tool_ids(result) == ["c1", "c2", "c3"]
    contents = {
        m["tool_call_id"]: m["content"]
        for m in result.messages
        if m.get("role") == "tool"
    }
    assert "content of c1.txt" in contents["c1"]
    assert "content of c3.txt" in contents["c3"]


def test_cancellation_still_writes_a_result_for_every_call(tmp_path: Path):
    runtime = _runtime(tmp_path)
    original = runtime.execute
    calls = []

    def execute(name, args=None, **kwargs):
        calls.append(name)
        output = original(name, args, **kwargs)
        # Cancel after the first call completes: the remaining calls were
        # already emitted to the transcript and must be answered.
        runtime.cancel()
        return output

    runtime.execute = execute  # type: ignore[method-assign]
    script = Script([
        _turn(
            ("c1", "write_file", {"path": "a.txt", "content": "1"}),
            ("c2", "write_file", {"path": "b.txt", "content": "2"}),
            ("c3", "write_file", {"path": "c.txt", "content": "3"}),
        ),
        {"role": "assistant", "content": "should not be reached"},
    ])

    result = AgentLoop(script, runtime=runtime, max_turns=4).run("go")

    assert result.stop_reason == STOP_CANCELLED
    assert calls == ["write_file"]
    assert _tool_ids(result) == ["c1", "c2", "c3"]
    contents = {
        m["tool_call_id"]: m["content"]
        for m in result.messages
        if m.get("role") == "tool"
    }
    assert contents["c2"] == CANCELLED_TOOL_OUTPUT
    assert contents["c3"] == CANCELLED_TOOL_OUTPUT
    assert not (tmp_path / "b.txt").exists()


def test_cancelled_while_parallel_reads_are_in_flight(tmp_path: Path):
    _prepare(tmp_path, "c1.txt", "c2.txt")
    runtime = _runtime(tmp_path)
    tracker = Tracker(runtime, on_call=lambda name, args: runtime.cancel())
    script = Script([
        _turn(_read("c1"), _read("c2")),
        {"role": "assistant", "content": "should not be reached"},
    ])

    result = AgentLoop(script, runtime=runtime, max_turns=4).run("go")

    # Both reads were dispatched before the cancel landed; both get results,
    # and the run reports cancellation rather than a clean completion.
    assert tracker.peak >= 2
    assert result.stop_reason == STOP_CANCELLED
    assert _tool_ids(result) == ["c1", "c2"]


def test_fewer_than_two_calls_never_uses_the_pool(tmp_path: Path):
    _prepare(tmp_path, "c1.txt")
    runtime = _runtime(tmp_path)
    tracker = Tracker(runtime)
    script = Script([
        _turn(_read("c1")),
        {"role": "assistant", "content": "ok"},
    ])

    AgentLoop(script, runtime=runtime, max_turns=4).run("go")

    assert tracker.peak == 1
