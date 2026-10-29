"""The loop: multi-turn continuation, protocol integrity, stops, events."""
from __future__ import annotations

import json
from pathlib import Path


from agent.context import ContextBudget
from agent.loop import (
    STOP_CANCELLED,
    STOP_COMPLETED,
    STOP_ERROR,
    STOP_MAX_TURNS,
    STOP_PERMISSION,
    STOP_REPEAT,
    AgentLoop,
    normalise_assistant_message,
)
from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from agent.session import AgentSession


def _runtime(tmp_path: Path, *, classes=None, headless=True, approver=None) -> AgentRuntime:
    data = {
        "classes": classes or {"read": "allow", "write": "allow", "exec": "allow", "native": "allow"},
        "paths": {"outside_workspace": "deny", "protected": "allow"},
    }
    return AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict(data),
        approver=approver,
        headless=headless,
    )


def _call(call_id: str, name: str, args: dict) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


class Script:
    """A completer that plays back a fixed list and records its inputs."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages, tools):
        self.calls.append(([dict(m) for m in messages], list(tools)))
        if not self.responses:
            return {"role": "assistant", "content": "(script exhausted)"}
        return self.responses.pop(0)


def test_loop_continues_after_a_tool_result(tmp_path: Path):
    script = Script([
        _call("c1", "write_file", {"path": "out.txt", "content": "hi"}),
        _call("c2", "read_file", {"path": "out.txt"}),
        {"role": "assistant", "content": "Done: wrote and read out.txt."},
    ])
    loop = AgentLoop(script, runtime=_runtime(tmp_path), max_turns=10)

    result = loop.run("create out.txt")

    assert result.stop_reason == STOP_COMPLETED
    assert result.turns == 3
    assert result.text.startswith("Done")
    assert result.usage["tool_calls"] == 2
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hi"
    assert len(script.calls) == 3

    # The second completion must have seen the first tool's result.
    second_messages = script.calls[1][0]
    assert any(m.get("role") == "tool" and "out.txt" in str(m.get("content")) for m in second_messages)


def test_every_assistant_tool_call_has_exactly_one_result(tmp_path: Path):
    script = Script([
        _call("c1", "write_file", {"path": "a.txt", "content": "1"}),
        _call("c2", "write_file", {"path": "b.txt", "content": "2"}),
        {"role": "assistant", "content": "ok"},
    ])
    result = AgentLoop(script, runtime=_runtime(tmp_path), max_turns=10).run("go")

    results: dict[str, int] = {}
    for message in result.messages:
        if message.get("role") == "tool":
            results[message["tool_call_id"]] = results.get(message["tool_call_id"], 0) + 1
    assert results == {"c1": 1, "c2": 1}
    assert [m["role"] for m in result.messages] == [
        "user", "assistant", "tool", "assistant", "tool", "assistant",
    ]


def test_identical_calls_are_skipped_then_end_the_run(tmp_path: Path):
    call = _call("c1", "read_file", {"path": "same.txt"})
    script = Script([call] * 20)
    loop = AgentLoop(script, runtime=_runtime(tmp_path), max_turns=20, repeat_limit=3)

    result = loop.run("loop forever")

    assert result.stop_reason == STOP_REPEAT
    assert result.turns == 4  # three runs, the fourth turn is all repeats
    skipped = [e for e in result.events if e["type"] == "tool_result" and "Identical call skipped" in e["preview"]]
    assert len(skipped) == 1


def test_max_turns_is_a_hard_stop(tmp_path: Path):
    def completer(messages, tools):
        turn = sum(1 for m in messages if m["role"] == "assistant")
        return _call(f"c{turn}", "write_file", {"path": f"f{turn}.txt", "content": "x"})

    loop = AgentLoop(completer, runtime=_runtime(tmp_path), max_turns=3)
    result = loop.run("keep going")

    assert result.stop_reason == STOP_MAX_TURNS
    assert result.turns == 3
    assert "(script" not in result.text
    assert "max_turns" in result.text


def test_a_denied_turn_stops_instead_of_retrying(tmp_path: Path):
    script = Script([_call("c1", "run_command", {"command": "pytest"})] * 5)
    runtime = _runtime(tmp_path, classes={"read": "allow", "write": "allow", "exec": "deny"})
    result = AgentLoop(script, runtime=runtime, max_turns=10).run("run the tests")

    assert result.stop_reason == STOP_PERMISSION
    assert result.turns == 1
    assert len(script.calls) == 1
    assert "permission" in result.text.lower()


def test_approver_can_allow_a_gated_call(tmp_path: Path):
    approvals = []

    def approve(name, args, detail):
        approvals.append(name)
        return True

    runtime = _runtime(tmp_path, classes={"exec": "ask", "write": "allow", "read": "allow"}, approver=approve)
    script = Script([
        _call("c1", "run_command", {"command": "echo approved"}),
        {"role": "assistant", "content": "ran it"},
    ])
    result = AgentLoop(script, runtime=runtime, max_turns=4).run("run echo")

    assert result.stop_reason == STOP_COMPLETED
    assert approvals == ["run_command"]


def test_cancellation_is_checked_between_turns(tmp_path: Path):
    import threading

    cancel = threading.Event()
    cancel.set()
    result = AgentLoop(Script([]), runtime=_runtime(tmp_path), cancel=cancel).run("go")
    assert result.stop_reason == STOP_CANCELLED


def test_completer_failure_becomes_a_stop_not_a_crash(tmp_path: Path):
    def explode(messages, tools):
        raise RuntimeError("endpoint down")

    result = AgentLoop(explode, runtime=_runtime(tmp_path)).run("go")
    assert result.stop_reason == STOP_ERROR
    assert "endpoint down" in result.text


def test_first_response_is_not_requested_twice(tmp_path: Path):
    script = Script([{"role": "assistant", "content": "finished"}])
    loop = AgentLoop(script, runtime=_runtime(tmp_path), max_turns=5)
    result = loop.run(
        first_response=_call("c1", "write_file", {"path": "seed.txt", "content": "x"})
    )

    assert result.stop_reason == STOP_COMPLETED
    assert result.turns == 2
    assert len(script.calls) == 1
    assert (tmp_path / "seed.txt").exists()


def test_compaction_runs_inside_a_long_loop(tmp_path: Path):
    counter = {"n": 0}

    def completer(messages, tools):
        counter["n"] += 1
        if counter["n"] >= 9:
            return {"role": "assistant", "content": "done"}
        return _call(f"c{counter['n']}", "write_file", {"path": f"f{counter['n']}.txt", "content": "x" * 40})

    budget = ContextBudget(max_chars=600, keep_recent_messages=4, max_tool_output_chars=200)
    result = AgentLoop(completer, runtime=_runtime(tmp_path), max_turns=12, budget=budget).run("many files")

    assert result.stop_reason == STOP_COMPLETED
    assert result.usage["compactions"] >= 1
    assert any(event["type"] == "compaction" for event in result.events)
    assert any(
        str(m.get("content", "")).startswith("[Earlier context — compacted]")
        for m in result.messages
    )


def test_event_stream_is_ordered_and_complete(tmp_path: Path):
    script = Script([
        _call("c1", "write_file", {"path": "e.txt", "content": "x"}),
        {"role": "assistant", "content": "done"},
    ])
    seen = []
    result = AgentLoop(script, runtime=_runtime(tmp_path), on_event=seen.append).run("go")

    kinds = [event["type"] for event in result.events]
    assert kinds[0] == "turn_start"
    assert "tool_call" in kinds and "tool_result" in kinds
    assert kinds[-1] == "stop"
    assert [e["type"] for e in seen] == kinds


def test_session_records_the_same_transcript(tmp_path: Path):
    session = AgentSession.create(tmp_path, db_path=tmp_path / "agent.sqlite3")
    script = Script([
        _call("c1", "write_file", {"path": "s.txt", "content": "x"}),
        {"role": "assistant", "content": "done"},
    ])
    result = AgentLoop(script, runtime=_runtime(tmp_path), session=session).run("go")

    stored = session.messages()
    assert stored == result.messages
    assert result.session_id == session.id


def test_normalise_accepts_the_three_provider_shapes():
    wrapped = {"choices": [{"message": {"content": "a", "tool_calls": [
        {"id": "1", "function": {"name": "read_file", "arguments": {"path": "x"}}}
    ]}}]}
    message = normalise_assistant_message(wrapped)
    assert message["tool_calls"][0]["function"]["arguments"] == '{"path": "x"}'

    plain = normalise_assistant_message({"message": {"content": "b"}})
    assert plain["content"] == "b" and "tool_calls" not in plain

    legacy = normalise_assistant_message({"content": "c", "function_call": {"name": "grep_files"}})
    assert legacy["tool_calls"][0]["function"]["name"] == "grep_files"


def test_native_gemini_declarations_are_converted_to_openai_tools(tmp_path: Path):
    declaration = {
        "name": "open_youtube",
        "description": "Open a video.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"url": {"type": "STRING"}},
            "required": ["url"],
        },
    }
    script = Script([{"role": "assistant", "content": "ok"}])
    AgentLoop(script, tools=[declaration], runtime=_runtime(tmp_path)).run("go")

    tool = script.calls[0][1][0]
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "open_youtube"
    assert tool["function"]["parameters"]["type"] == "object"
    assert tool["function"]["parameters"]["properties"]["url"]["type"] == "string"


def test_registered_tools_convert_through_their_spec(tmp_path: Path):
    script = Script([{"role": "assistant", "content": "ok"}])
    AgentLoop(script, tools=["read_file"], runtime=_runtime(tmp_path)).run("go")

    tool = script.calls[0][1][0]
    assert tool["function"]["name"] == "read_file"
    assert tool["function"]["parameters"]["type"] == "object"
