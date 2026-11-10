"""Subagents: a bounded child loop behind one tool call.

Pure Python on purpose: the harness CI installs only pytest, so this file must
not import PyQt or a provider SDK. The "model" is a scripted completer shared
by the parent and the child, which makes the delegation order explicit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.loop import STOP_COMPLETED, STOP_PERMISSION, AgentLoop
from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from agent.spec import ToolContext, ToolSpec, get as get_spec
from agent.subagent import (
    DEFAULT_SUBAGENT_TURNS,
    DELEGATION_TOOLS,
    MAX_BATCH_TASKS,
    MAX_SUBAGENT_TURNS,
    SUBAGENT_BATCH_TOOL,
    SUBAGENT_TOOL,
    clamp_turns,
    run_subagent,
    run_subagent_batch,
    select_batch_tools,
)


def _runtime(tmp_path: Path, *, classes=None) -> AgentRuntime:
    data = {
        "classes": classes
        or {"read": "allow", "write": "allow", "exec": "allow", "native": "allow"},
        "paths": {"outside_workspace": "deny", "protected": "allow"},
    }
    return AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict(data),
        approver=None,
        headless=True,
    )


def _turn(*calls) -> dict:
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


def _final(text: str) -> dict:
    return {"role": "assistant", "content": text}


class Script:
    """Serve queued responses and remember what each loop was offered."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.tool_names = []

    def __call__(self, messages, tools):
        self.calls.append([dict(message) for message in messages])
        self.tool_names.append(
            [entry["function"]["name"] for entry in tools]
        )
        if not self.responses:
            return _final("done")
        return self.responses.pop(0)


def _tool_output(result, call_id: str) -> str:
    for message in result.messages:
        if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
            return str(message.get("content") or "")
    raise AssertionError(f"no tool result for {call_id!r}")


def _delegate(**args) -> tuple:
    return ("c1", SUBAGENT_TOOL, {"task": "read a.txt", **args})


def test_subagent_completes_a_task_and_returns_a_report(tmp_path: Path):
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    runtime = _runtime(tmp_path)
    script = Script([
        _turn(_delegate()),
        _turn(("s1", "read_file", {"path": "a.txt"})),
        _final("a.txt contains alpha"),
        _final("the parent is done"),
    ])

    result = AgentLoop(script, runtime=runtime, max_turns=5).run("delegate")

    assert result.stop_reason == STOP_COMPLETED
    report = _tool_output(result, "c1")
    assert "Subagent subagent-1 completed" in report
    assert "a.txt contains alpha" in report
    # The child's transcript is working memory: only its report crosses back.
    tools = [m for m in result.messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["c1"]
    # One parent tool call; the child's own read is not counted again.
    assert result.usage["tool_calls"] == 1


def test_child_surface_is_read_only_and_never_recursive(tmp_path: Path):
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    runtime = _runtime(tmp_path)
    script = Script([
        _turn(_delegate()),
        _final("nothing to add"),
        _final("done"),
    ])

    AgentLoop(script, runtime=runtime, max_turns=4).run("delegate")

    # The parent can delegate; the child cannot delegate again and does not
    # inherit write/exec primitives it was not asked to use.
    assert SUBAGENT_TOOL in script.tool_names[0]
    # `load_project_skill` is read-class too, and the child shares the parent's
    # system prompt, so it can follow a project skill without mutating anything.
    # The native web tools are network-class; the default policy allows
    # `network`, so they travel with the read surface and give a child the same
    # research ability as its parent without write/exec access.
    assert script.tool_names[1] == [
        "brain_browse",
        "brain_locate",
        "brain_read",
        "brain_search",
        "brain_status",
        "glob_files",
        "grep_files",
        "load_project_skill",
        "read_file",
        "web_fetch",
        "web_search",
    ]


def test_requested_tools_are_intersected_with_the_parent_surface(tmp_path: Path):
    runtime = _runtime(tmp_path)
    script = Script([
        _turn(_delegate(tools=["write_file", SUBAGENT_TOOL, "not_a_tool"])),
        _turn(("s1", "write_file", {"path": "out.txt", "content": "from child"})),
        _final("wrote out.txt"),
        _final("done"),
    ])

    result = AgentLoop(script, runtime=runtime, max_turns=4).run("delegate")

    assert script.tool_names[1] == ["write_file"]
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "from child"
    assert "wrote out.txt" in _tool_output(result, "c1")


def test_an_unknown_request_does_not_start_a_child(tmp_path: Path):
    runtime = _runtime(tmp_path)
    script = Script([
        _turn(_delegate(tools=["not_a_tool", SUBAGENT_TOOL])),
        _final("could not delegate"),
    ])

    result = AgentLoop(script, runtime=runtime, max_turns=4).run("delegate")

    # The refusal is the whole turn: a turn whose every call is refused ends
    # the run (STOP_PERMISSION), exactly like a denied command. The point is
    # that no child request was ever made.
    assert len(script.calls) == 1
    assert "subagent_start" not in [e.get("type") for e in result.events]
    assert "Not run: none of the requested tools" in _tool_output(result, "c1")
    assert result.stop_reason == STOP_PERMISSION


def test_child_context_is_fresh_and_contains_the_briefing(tmp_path: Path):
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    runtime = _runtime(tmp_path)
    script = Script([
        _turn(_delegate(context="remember the ticket number 42")),
        _final("ok"),
        _final("done"),
    ])

    AgentLoop(script, runtime=runtime, max_turns=4).run("parent-only prompt")

    child_messages = script.calls[1]
    assert child_messages[0]["role"] == "system"
    assert "focused subagent" in child_messages[0]["content"]
    briefing = child_messages[-1]["content"]
    assert "read a.txt" in briefing
    assert "remember the ticket number 42" in briefing
    # The child starts from its own brief, not from the parent conversation.
    assert all("parent-only prompt" not in str(m.get("content")) for m in child_messages)


def test_child_calls_use_the_same_policy(tmp_path: Path):
    runtime = _runtime(
        tmp_path,
        classes={"read": "allow", "write": "deny", "exec": "deny", "native": "allow"},
    )
    script = Script([
        _turn(_delegate(tools=["write_file"])),
        _turn(("s1", "write_file", {"path": "out.txt", "content": "nope"})),
        _final("done"),
    ])

    result = AgentLoop(script, runtime=runtime, max_turns=4).run("delegate")

    report = _tool_output(result, "c1")
    assert "permission_denied" in report
    assert "partial" in report
    assert not (tmp_path / "out.txt").exists()


def test_a_child_failure_becomes_a_report_not_an_exception(tmp_path: Path):
    runtime = _runtime(tmp_path)
    script = Script([_turn(_delegate()), _final("done")])

    calls = {"count": 0}

    def complete(messages, tools):
        # The parent's first request is served from the script; the child's
        # request raises inside its own loop. The system prompt starts with
        # the shared base, so detect the child by its appended brief.
        if any("focused subagent" in str(m.get("content") or "") for m in messages):
            calls["count"] += 1
            raise RuntimeError("provider exploded")
        return script(messages, tools)

    result = AgentLoop(complete, runtime=runtime, max_turns=4).run("delegate")

    report = _tool_output(result, "c1")
    assert calls["count"] == 1
    assert "error" in report
    assert "provider exploded" in report
    assert result.stop_reason == STOP_COMPLETED


def test_child_turn_budget_is_clamped_and_reported(tmp_path: Path):
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    runtime = _runtime(tmp_path)
    script = Script([
        _turn(_delegate(max_turns=1)),
        _turn(("s1", "read_file", {"path": "a.txt"})),
        _final("done"),
    ])

    result = AgentLoop(script, runtime=runtime, max_turns=4).run("delegate")

    # One child turn was spent on the read; the child never gets a second.
    assert len(script.calls) == 3
    report = _tool_output(result, "c1")
    assert "max_turns" in report
    assert "partial" in report


def test_clamp_turns_keeps_a_budget_small_and_positive():
    assert clamp_turns(999) == MAX_SUBAGENT_TURNS
    assert clamp_turns("3") == 3
    assert clamp_turns(0) == 1
    assert clamp_turns(None) == DEFAULT_SUBAGENT_TURNS
    assert clamp_turns("nonsense") == DEFAULT_SUBAGENT_TURNS


def test_the_handler_refuses_without_a_loop_runner(tmp_path: Path):
    context = ToolContext(workspace=tmp_path)
    assert "needs an agent loop" in run_subagent({"task": "x"}, context)

    # The spec is registered, so a direct runtime call reaches the same guard
    # instead of reporting an unknown tool.
    runtime = _runtime(tmp_path)
    assert get_spec(SUBAGENT_TOOL) is not None
    assert "needs an agent loop" in runtime.execute(SUBAGENT_TOOL, {"task": "x"})


def test_child_events_are_relayed_under_the_parent(tmp_path: Path):
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    runtime = _runtime(tmp_path)
    script = Script([
        _turn(_delegate()),
        _turn(("s1", "read_file", {"path": "a.txt"})),
        _final("alpha"),
        _final("done"),
    ])

    result = AgentLoop(script, runtime=runtime, max_turns=4).run("delegate")

    kinds = [event.get("type") for event in result.events]
    assert "subagent_start" in kinds
    assert "subagent_event" in kinds
    assert "subagent_end" in kinds

    relayed = [
        event["event"]
        for event in result.events
        if event.get("type") == "subagent_event"
    ]
    tool_calls = [e for e in relayed if e.get("type") == "tool_call"]
    assert [e.get("name") for e in tool_calls] == ["read_file"]
    end = next(e for e in result.events if e.get("type") == "subagent_end")
    assert end["stop_reason"] == STOP_COMPLETED
    assert end["turns"] == 2


@pytest.mark.parametrize("task", ["", "   "])
def test_an_empty_task_is_refused(tmp_path: Path, task: str):
    runtime = _runtime(tmp_path)
    script = Script([_turn(("c1", SUBAGENT_TOOL, {"task": task})), _final("done")])

    result = AgentLoop(script, runtime=runtime, max_turns=4).run("delegate")

    # The refused call is the only call, so the turn ends at STOP_PERMISSION;
    # no child was ever started.
    assert "non-empty 'task'" in _tool_output(result, "c1")
    assert len(script.calls) == 1
    assert result.stop_reason == STOP_PERMISSION


def test_select_batch_tools_enforces_strict_read_only_surface():
    read_ok = ToolSpec(
        name="custom_read",
        description="safe read",
        parameters={"type": "object", "properties": {}},
        tool_class="read",
        handler=lambda a, c: "ok",
    )
    read_approval = ToolSpec(
        name="approval_read",
        description="needs approval",
        parameters={"type": "object", "properties": {}},
        tool_class="read",
        requires_approval=True,
        handler=lambda a, c: "ok",
    )
    read_classified = ToolSpec(
        name="classified_tool",
        description="classified",
        parameters={"type": "object", "properties": {}},
        tool_class="read",
        classifier=lambda a: "read",
        handler=lambda a, c: "ok",
    )
    mcp_tool = ToolSpec(
        name="mcp_srv_lookup",
        description="mcp",
        parameters={"type": "object", "properties": {}},
        tool_class="mcp",
        handler=lambda a, c: "ok",
    )
    write_tool = get_spec("write_file")
    sub_single = get_spec(SUBAGENT_TOOL)
    sub_batch = get_spec(SUBAGENT_BATCH_TOOL)

    parent_surface = [
        read_ok,
        read_approval,
        read_classified,
        mcp_tool,
        write_tool,
        sub_single,
        sub_batch,
    ]
    chosen = select_batch_tools(parent_surface)
    assert [s.name for s in chosen] == ["custom_read"]

    # Even if explicitly requested, non-read / approval / mcp / delegation tools are excluded
    chosen_req = select_batch_tools(
        parent_surface,
        requested=[
            "custom_read",
            "approval_read",
            "classified_tool",
            "mcp_srv_lookup",
            "write_file",
            SUBAGENT_TOOL,
            SUBAGENT_BATCH_TOOL,
        ],
    )
    assert [s.name for s in chosen_req] == ["custom_read"]


def test_subagent_batch_runs_read_only_children_and_returns_ordered_reports(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "a.txt").write_text("alpha-content", encoding="utf-8")
    (ws / "b.txt").write_text("beta-content", encoding="utf-8")

    runtime = _runtime(ws)
    seen_child_surfaces = []

    def complete(messages, tools):
        names = [entry["function"]["name"] for entry in tools]
        is_child = any("focused subagent" in str(m.get("content") or "") for m in messages)
        if not is_child:
            has_tool_result = any(m.get("role") == "tool" for m in messages)
            if not has_tool_result:
                return _turn(
                    (
                        "b1",
                        SUBAGENT_BATCH_TOOL,
                        {
                            "tasks": [
                                {"task": "inspect a.txt"},
                                {"task": "inspect b.txt"},
                            ],
                            "context": "shared batch note",
                            "max_concurrency": 2,
                        },
                    )
                )
            return _final("batch parent finished")

        seen_child_surfaces.append(names)
        user_prompt = next(m["content"] for m in reversed(messages) if m.get("role") == "user")
        target = "a.txt" if "a.txt" in user_prompt else "b.txt"
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if not tool_msgs:
            return _turn(("r1", "read_file", {"path": target}))
        return _final(f"report for {target}: {tool_msgs[-1]['content'].splitlines()[-1]}")

    result = AgentLoop(complete, runtime=runtime, max_turns=5).run("run batch")

    assert result.stop_reason == STOP_COMPLETED
    output = _tool_output(result, "b1")
    assert "Subagent batch completed (2 task(s)):" in output
    idx_a = output.index("=== Task 1 (subagent-1): inspect a.txt ===")
    idx_b = output.index("=== Task 2 (subagent-2): inspect b.txt ===")
    assert idx_a < idx_b
    assert "alpha-content" in output
    assert "beta-content" in output

    # Every child surface excludes both delegation tools and all non-read tools
    assert len(seen_child_surfaces) >= 2
    for surface in seen_child_surfaces:
        for forbidden in (*DELEGATION_TOOLS, "write_file", "edit_file", "run_command"):
            assert forbidden not in surface
        assert surface == [
            "brain_browse",
            "brain_locate",
            "brain_read",
            "brain_search",
            "brain_status",
            "glob_files",
            "grep_files",
            "load_project_skill",
            "read_file",
        ]

    kinds = [e.get("type") for e in result.events]
    assert "subagent_batch_start" in kinds
    assert "subagent_batch_end" in kinds


def test_subagent_batch_enforces_workspace_confinement_and_blocks_protected_paths(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("outside-secret", encoding="utf-8")
    (ws / ".env").write_text("API_KEY=topsecret", encoding="utf-8")

    approver_calls = []

    # Even when the parent policy allows outside_workspace, protected, trusted_roots,
    # and has a live approver + session_allow, batch children are strictly denied
    # outside-workspace and protected reads without ever calling the approver.
    policy = PermissionPolicy.from_dict(
        {
            "classes": {"read": "allow", "write": "allow", "exec": "allow", "native": "allow"},
            "paths": {
                "outside_workspace": "allow",
                "protected": "allow",
                "trusted_roots": [str(outside_dir)],
            },
        }
    )
    runtime = AgentRuntime(
        workspace=ws,
        policy=policy,
        approver=lambda name, args, detail: (approver_calls.append(name) or "allow"),
        headless=False,
    )
    runtime.allow_for_session("read_file", {"path": str(outside_file)}, rule="paths.outside_workspace")
    runtime.allow_for_session("read_file", {"path": ".env"}, rule="paths.protected")

    def complete(messages, tools):
        is_child = any("focused subagent" in str(m.get("content") or "") for m in messages)
        if not is_child:
            if not any(m.get("role") == "tool" for m in messages):
                return _turn(
                    (
                        "b1",
                        SUBAGENT_BATCH_TOOL,
                        {
                            "tasks": [
                                {"task": "read outside"},
                                {"task": "read dotenv"},
                                {"task": "try write"},
                            ],
                        },
                    )
                )
            return _final("done")

        user_prompt = next(m["content"] for m in reversed(messages) if m.get("role") == "user")
        if any(m.get("role") == "tool" for m in messages):
            return _final("blocked as expected")
        if "read outside" in user_prompt:
            return _turn(("s1", "read_file", {"path": str(outside_file)}))
        if "read dotenv" in user_prompt:
            return _turn(("s2", "read_file", {"path": ".env"}))
        # Even if a child hallucinates an unadvertised write_file call:
        return _turn(("s3", "write_file", {"path": "pwn.txt", "content": "nope"}))

    result = AgentLoop(complete, runtime=runtime, max_turns=4).run("test confinement")

    assert result.stop_reason == STOP_COMPLETED
    assert approver_calls == []
    assert not (ws / "pwn.txt").exists()

    output = _tool_output(result, "b1")
    assert "Subagent subagent-1 permission_denied" in output
    assert "Subagent subagent-2 permission_denied" in output
    assert "Subagent subagent-3 permission_denied" in output
    assert "outside-secret" not in output
    assert "topsecret" not in output


def test_subagent_batch_refuses_invalid_or_mutating_tool_requests(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    runtime = _runtime(ws)

    # 1. Requesting only non-read/delegation tools refuses before any child starts
    script = Script([
        _turn(
            (
                "b1",
                SUBAGENT_BATCH_TOOL,
                {
                    "tasks": [{"task": "mutate"}],
                    "tools": ["write_file", "run_command", SUBAGENT_TOOL, SUBAGENT_BATCH_TOOL],
                },
            )
        ),
        _final("done"),
    ])
    result = AgentLoop(script, runtime=runtime, max_turns=4).run("batch")
    assert len(script.calls) == 1
    assert "none of the requested read-only workspace tools" in _tool_output(result, "b1")
    assert result.stop_reason == STOP_PERMISSION

    # 2. Exceeding MAX_BATCH_TASKS or calling without a loop runner is refused
    ctx = ToolContext(workspace=ws)
    assert "needs an agent loop" in run_subagent_batch({"tasks": [{"task": "x"}]}, ctx)
    too_many = [{"task": f"t{i}"} for i in range(MAX_BATCH_TASKS + 1)]
    assert f"at most {MAX_BATCH_TASKS}" in run_subagent_batch(
        {"tasks": too_many},
        ToolContext(workspace=ws, extra={"subagent_batch_runner": lambda a: "ok"}),
    )

