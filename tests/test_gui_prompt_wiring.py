"""The GUI text path must carry the harness project prompt.

``agent.prompts.build_system_prompt`` is what gives the CLI and subagents the
workspace's ``AGENTS.md``/``BRAHMA.md`` and the project-skills catalogue. The
HUD builds its own resident prompt, so this file checks the wiring in
``main.py`` that appends the same blocks. It imports ``main``, so it is
deliberately *not* named ``test_agent_*`` and stays out of the stdlib-only
harness CI job.
"""
from __future__ import annotations

import types


def _workspace(tmp_path):
    (tmp_path / "AGENTS.md").write_text("GUI-PROJECT-RULE", encoding="utf-8")
    skill = tmp_path / ".brahma" / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: demo\ndescription: A demo skill.\n---\n\nBody.\n",
        encoding="utf-8",
    )


def _seed(monkeypatch, tmp_path, *, advertise_discovery: bool = True) -> str:
    import main as gui

    monkeypatch.setattr(
        "agent.runtime.current", lambda: types.SimpleNamespace(workspace=tmp_path)
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    dummy = types.SimpleNamespace(_agent_session=None)
    messages = gui.BrahmaLive._agentic_seed_messages(
        dummy, "hello", advertise_discovery=advertise_discovery
    )
    assert messages[0]["role"] == "system"
    return messages[0]["content"]


def test_the_text_path_prompt_has_project_rules_and_skills(tmp_path, monkeypatch):
    _workspace(tmp_path)

    system = _seed(monkeypatch, tmp_path)

    assert "GUI-PROJECT-RULE" in system
    assert "## Project skills" in system
    assert "`demo`: A demo skill." in system
    assert system.index("GUI-PROJECT-RULE") < system.index("## Project skills")


def test_the_prompt_keeps_project_context_without_tool_discovery(tmp_path, monkeypatch):
    """An endpoint that rejects tool arrays still gets the project blocks."""
    _workspace(tmp_path)

    system = _seed(monkeypatch, tmp_path, advertise_discovery=False)

    assert "GUI-PROJECT-RULE" in system
    assert "## Project skills" in system


def test_a_workspace_without_files_adds_no_project_section(tmp_path, monkeypatch):
    system = _seed(monkeypatch, tmp_path)

    assert "GUI-PROJECT-RULE" not in system
    assert "## Project skills" not in system


def test_the_seed_still_uses_the_configured_runtime_workspace(tmp_path, monkeypatch):
    """The workspace comes from the runtime, not the process cwd."""
    other = tmp_path / "other"
    other.mkdir()
    (other / "AGENTS.md").write_text("OTHER-RULE", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("RUNTIME-RULE", encoding="utf-8")

    monkeypatch.chdir(other)
    monkeypatch.setattr(
        "agent.runtime.current", lambda: types.SimpleNamespace(workspace=workspace)
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    import main as gui

    dummy = types.SimpleNamespace(_agent_session=None)
    system = gui.BrahmaLive._agentic_seed_messages(dummy, "hello")[0]["content"]

    assert "RUNTIME-RULE" in system
    assert "OTHER-RULE" not in system
