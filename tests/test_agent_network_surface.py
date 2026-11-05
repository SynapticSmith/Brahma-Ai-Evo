"""The advertised tool surface follows a hard ``network`` denial.

`web_search`/`web_fetch` are useful to a `worker` token and dangerous to a
`reader`/`chat` one. The dispatch boundary already denies the call, but
advertising a tool that can only ever be denied wastes prompt budget and invites
the model to try it. Only a hard `deny` hides it: an `ask` still advertises the
tool so the CLI/HUD can approve it.
"""
from __future__ import annotations

from pathlib import Path

from agent.loop import AgentLoop
from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime


def _names(tools):
    names = []
    for item in tools or []:
        if isinstance(item, dict):
            function = item.get("function") or {}
            names.append(str(function.get("name") or ""))
        else:
            names.append(str(getattr(item, "name", "") or ""))
    return names


def _runtime(tmp_path: Path, classes=None) -> AgentRuntime:
    data = {"classes": classes or {}}
    return AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict(data),
        headless=True,
    )


def _capture_run(runtime, prompt="hello"):
    seen = []

    def complete(messages, tools):
        seen.append(_names(tools))
        return {"role": "assistant", "content": "done"}

    AgentLoop(complete, runtime=runtime).run(prompt)
    return seen


def test_a_hard_network_denial_hides_the_web_tools(tmp_path: Path):
    runtime = _runtime(tmp_path, {"network": "deny"})

    seen = _capture_run(runtime)

    assert seen
    assert "web_search" not in seen[0]
    assert "web_fetch" not in seen[0]
    # The filter is scoped to network: filesystem reads stay available.
    assert "read_file" in seen[0]


def test_an_allowed_network_class_keeps_the_web_tools(tmp_path: Path):
    runtime = _runtime(tmp_path, {"network": "allow"})

    seen = _capture_run(runtime)

    assert "web_search" in seen[0]
    assert "web_fetch" in seen[0]


def test_an_ask_still_advertises_the_web_tools(tmp_path: Path):
    runtime = _runtime(tmp_path, {"network": "ask"})

    seen = _capture_run(runtime)

    assert "web_search" in seen[0]
    assert "web_fetch" in seen[0]


def test_an_explicit_surface_is_not_filtered(tmp_path: Path):
    runtime = _runtime(tmp_path, {"network": "deny"})
    seen = []

    def complete(messages, tools):
        seen.append(_names(tools))
        return {"role": "assistant", "content": "done"}

    AgentLoop(complete, tools=["web_search", "read_file"], runtime=runtime).run("hi")

    # An explicit list is a caller decision; dispatch still denies the call.
    assert seen[0] == ["web_search", "read_file"]
