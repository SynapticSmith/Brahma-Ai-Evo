"""Provider-reported token usage: read it, never invent it."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agent.context import ContextBudget
from agent.loop import AgentLoop
from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from agent.usage import add_usage, extract_usage


# -- normalising provider shapes ------------------------------------------- #


def test_openai_usage_is_read_as_reported():
    response = {
        "role": "assistant",
        "content": "hi",
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
        },
    }
    assert extract_usage(response) == {
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
    }


def test_gemini_usage_metadata_is_understood():
    response = {
        "usage_metadata": {
            "prompt_token_count": 88,
            "candidates_token_count": 12,
            "total_token_count": 100,
        }
    }
    assert extract_usage(response) == {
        "prompt_tokens": 88,
        "completion_tokens": 12,
        "total_tokens": 100,
    }


def test_sdk_objects_with_attributes_work_too():
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=7, completion_tokens=3, total_tokens=10
        )
    )
    assert extract_usage(response) == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
    }


def test_absent_usage_is_empty_not_zero():
    assert extract_usage({"role": "assistant", "content": "hi"}) == {}
    assert extract_usage({}) == {}
    assert extract_usage(None) == {}
    assert extract_usage({"usage": {}}) == {}


def test_a_missing_total_is_summed_only_when_both_halves_exist():
    summed = extract_usage({"usage": {"prompt_tokens": 5, "completion_tokens": 6}})
    assert summed == {
        "prompt_tokens": 5,
        "completion_tokens": 6,
        "total_tokens": 11,
    }

    # One half is not enough: a total would be a guess.
    partial = extract_usage({"usage": {"prompt_tokens": 5}})
    assert partial == {"prompt_tokens": 5}
    assert "total_tokens" not in partial


def test_nonsense_counts_are_ignored():
    assert extract_usage(
        {"usage": {"prompt_tokens": -4, "completion_tokens": True, "total_tokens": "many"}}
    ) == {}
    # A numeric string is still a provider-reported count.
    assert extract_usage({"usage": {"prompt_tokens": "42"}}) == {"prompt_tokens": 42}


def test_add_usage_accumulates_in_place_and_skips_unknowns():
    total = {"prompt_tokens": 10}
    add_usage(total, {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7})
    add_usage(total, {})
    add_usage(total, {"completion_tokens": -1, "total_tokens": "x"})
    assert total == {"prompt_tokens": 15, "completion_tokens": 2, "total_tokens": 7}


# -- the loop reports what the provider said -------------------------------- #


def _runtime(tmp_path: Path, sink=None) -> AgentRuntime:
    return AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict(
            {"classes": {"read": "allow", "write": "allow", "exec": "allow", "native": "allow"}}
        ),
        headless=True,
        event_sink=sink,
    )


def _usage(prompt: int, completion: int) -> dict:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def test_loop_sums_usage_across_turns(tmp_path: Path):
    reply = {"role": "assistant", "content": "done", "usage": _usage(100, 20)}
    seen = []

    def complete(messages, tools):
        seen.append(1)
        return dict(reply)

    loop = AgentLoop(complete, runtime=_runtime(tmp_path), max_turns=1)
    result = loop.run("hello")

    assert result.usage["prompt_tokens"] == 100
    assert result.usage["completion_tokens"] == 20
    assert result.usage["total_tokens"] == 120
    assert result.usage["tokens_reported"] is True
    # The local estimate is still there and independent of the real counts.
    assert "estimated_tokens" in result.usage


def test_loop_reports_unknown_usage_without_inventing_numbers(tmp_path: Path):
    loop = AgentLoop(
        lambda messages, tools: {"role": "assistant", "content": "done"},
        runtime=_runtime(tmp_path),
        max_turns=1,
    )
    result = loop.run("hello")

    assert result.usage["prompt_tokens"] == 0
    assert result.usage["completion_tokens"] == 0
    assert result.usage["total_tokens"] == 0
    assert result.usage["tokens_reported"] is False


def test_each_assistant_event_carries_that_turns_usage(tmp_path: Path):
    events = []
    responses = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"path": "a.txt", "content": "1"}),
                    },
                }
            ],
            "usage": _usage(10, 5),
        },
        {"role": "assistant", "content": "done", "usage": _usage(20, 7)},
    ]

    def complete(messages, tools):
        return responses.pop(0)

    loop = AgentLoop(
        complete,
        runtime=_runtime(tmp_path, sink=events.append),
        max_turns=5,
        budget=ContextBudget(),
    )
    result = loop.run("write a.txt then stop")

    assistant = [e for e in events if e.get("type") == "assistant"]
    assert [e.get("usage") for e in assistant] == [_usage(10, 5), _usage(20, 7)]
    # Accumulated result is the sum, not the last response.
    assert result.usage["total_tokens"] == 42
    assert result.usage["tokens_reported"] is True


def test_an_event_without_usage_has_no_usage_key(tmp_path: Path):
    events = []
    loop = AgentLoop(
        lambda messages, tools: {"role": "assistant", "content": "done"},
        runtime=_runtime(tmp_path, sink=events.append),
        max_turns=1,
        budget=ContextBudget(),
    )
    loop.run("hello")

    assistant = [e for e in events if e.get("type") == "assistant"]
    assert assistant and "usage" not in assistant[0]
