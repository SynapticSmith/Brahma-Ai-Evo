"""Context accounting and compaction that cannot corrupt the tool protocol."""
from __future__ import annotations

import json

from agent.context import (
    SUMMARY_MARKER,
    ContextBudget,
    ConversationContext,
    estimate_tokens,
    truncate_tool_output,
)


def _tool_turn(call_id: str, name: str = "read_file") -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps({"path": f"{name}.py"})},
                }
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "name": name, "content": "x" * 10},
    ]


def test_estimate_tokens_is_character_based():
    assert estimate_tokens("a" * 400) == 100


def test_truncation_keeps_the_tail():
    text = "HEAD " + ("m" * 5000) + " TAIL-VERDICT"
    out = truncate_tool_output(text, 1000)
    assert out.startswith("HEAD")
    assert out.endswith("TAIL-VERDICT")
    assert len(out) <= 1100


def test_no_compaction_below_budget():
    ctx = ConversationContext(system="sys", budget=ContextBudget(max_chars=10_000))
    ctx.append_user("hi")
    assert ctx.compact() is None


def test_compaction_keeps_a_safe_boundary():
    ctx = ConversationContext(system="sys", budget=ContextBudget(max_chars=1, keep_recent_messages=2))
    ctx.append_user("first request")
    for index in range(3):
        for message in _tool_turn(f"call_{index}"):
            ctx.append(message)
    ctx.append({"role": "user", "content": "latest request"})

    summary = ctx.compact()
    assert summary
    assert ctx.compactions == 1
    assert ctx.messages[0]["content"].startswith(SUMMARY_MARKER)
    assert ctx.messages[0]["role"] == "user"
    # No orphaned tool result at the boundary.
    assert ctx.messages[1]["role"] != "tool"


def test_every_retained_tool_call_has_its_result():
    ctx = ConversationContext(budget=ContextBudget(max_chars=1, keep_recent_messages=2))
    ctx.append_user("go")
    for index in range(4):
        for message in _tool_turn(f"call_{index}"):
            ctx.append(message)

    ctx.compact()

    pending = set()
    for message in ctx.messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                pending.add(call["id"])
        elif message.get("role") == "tool":
            pending.discard(message.get("tool_call_id"))
    assert pending == set()


def test_system_message_appears_exactly_once():
    ctx = ConversationContext(system="be terse")
    ctx.append_user("hi")
    payload = ctx.as_payload()
    assert [m["role"] for m in payload].count("system") == 1
    assert payload[0]["content"] == "be terse"


def test_deterministic_summary_records_requests_tools_and_files():
    ctx = ConversationContext(budget=ContextBudget(max_chars=1, keep_recent_messages=2))
    ctx.append_user("fix the parser")
    ctx.append(_tool_turn("c1", "edit_file")[0])
    ctx.append(_tool_turn("c1", "edit_file")[1])
    ctx.append_user("now run the tests")
    ctx.append_user("and commit")

    summary = ctx.compact()
    assert "fix the parser" in summary
    assert "edit_file" in summary
    assert "edit_file.py" in summary


def test_custom_summarizer_is_used_when_it_works():
    ctx = ConversationContext(budget=ContextBudget(max_chars=1, keep_recent_messages=2))
    ctx.append_user("old")
    ctx.append_user("middle")
    ctx.append_user("new")
    summary = ctx.compact(lambda rendered: "CUSTOM-SUMMARY")
    assert summary == "CUSTOM-SUMMARY"
    assert "CUSTOM-SUMMARY" in ctx.messages[0]["content"]


def test_a_failing_summarizer_falls_back_instead_of_raising():
    ctx = ConversationContext(budget=ContextBudget(max_chars=1, keep_recent_messages=2))
    ctx.append_user("old")
    ctx.append_user("middle")
    ctx.append_user("new")

    def explode(rendered: str) -> str:
        raise RuntimeError("model down")

    summary = ctx.compact(explode)
    assert summary
    assert "old" in summary
