"""The Gemini Live prompt must carry the harness project prompt.

The Live system instruction is assembled in ``core/session_lifecycle.py``.
``agent.prompts.build_system_prompt`` gives the CLI and subagents the
workspace's ``AGENTS.md``/``BRAHMA.md`` and the project-skills catalogue, so the
voice path appends the same blocks there. This file imports the Live module, so
it is deliberately *not* named ``test_agent_*`` and stays out of the
stdlib-only harness CI job.
"""
from __future__ import annotations

import types

import pytest

import core.session_lifecycle as lifecycle_module
from core.session_lifecycle import SessionLifecycle


@pytest.fixture(autouse=True)
def _stubs(monkeypatch, tmp_path):
    """Keep memory, the core prompt and tool discovery out of the assertion."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.delenv("BRAHMA_SKILLS_DIR", raising=False)
    # These three normally arrive through ``SessionLifecycle(deps=...)`` from
    # main.py, so they are not module attributes until a host provides them.
    monkeypatch.setattr(
        lifecycle_module, "_load_system_prompt", lambda: "CORE-PROMPT", raising=False
    )
    monkeypatch.setattr(
        lifecycle_module, "_injected_instructions", lambda layer: "", raising=False
    )
    monkeypatch.setattr(lifecycle_module, "TOOL_DECLARATIONS", [], raising=False)
    monkeypatch.setattr(lifecycle_module, "load_memory", lambda: {})
    monkeypatch.setattr(lifecycle_module, "format_memory_for_prompt", lambda memory: "")


def _instruction(tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(
        "agent.runtime.current", lambda: types.SimpleNamespace(workspace=tmp_path)
    )
    lifecycle = SessionLifecycle(types.SimpleNamespace(), deps={})
    return lifecycle._build_config().system_instruction


def test_the_live_prompt_has_project_rules_and_skills(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("VOICE-PROJECT-RULE", encoding="utf-8")
    skill = tmp_path / ".brahma" / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: demo\ndescription: A voice skill.\n---\n\nBody.\n",
        encoding="utf-8",
    )

    instruction = _instruction(tmp_path, monkeypatch)

    assert "VOICE-PROJECT-RULE" in instruction
    assert "## Project skills" in instruction
    assert "`demo`: A voice skill." in instruction
    # The resident core prompt still comes first; project rules follow it.
    assert instruction.index("CORE-PROMPT") < instruction.index("VOICE-PROJECT-RULE")


def test_the_live_prompt_without_project_files_adds_no_section(tmp_path, monkeypatch):
    instruction = _instruction(tmp_path, monkeypatch)

    assert "VOICE-PROJECT-RULE" not in instruction
    assert "## Project skills" not in instruction
