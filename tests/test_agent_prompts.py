from __future__ import annotations

from pathlib import Path

from agent.prompts import (
    MAX_PROJECT_CHARS,
    PROJECT_FILES,
    build_system_prompt,
    instruction_dirs,
    load_project_instructions,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_workspace_instruction_files_are_loaded_in_priority_order(tmp_path: Path):
    _write(tmp_path / "AGENTS.md", "root rules")
    _write(tmp_path / "BRAHMA.md", "brahma rules")
    _write(tmp_path / ".brahma" / "AGENTS.md", "nested rules")

    text = load_project_instructions(tmp_path, max_parents=0)

    assert text.index("root rules") < text.index("brahma rules") < text.index("nested rules")
    for name in PROJECT_FILES:
        assert f"# Project instructions ({name.replace(chr(92), '/')})" in text


def test_parent_directories_are_read_nearest_last(tmp_path: Path):
    repo = tmp_path / "monorepo"
    package = repo / "packages" / "tool"
    package.mkdir(parents=True)
    _write(repo / "AGENTS.md", "monorepo rules")
    _write(package / "AGENTS.md", "package rules")

    text = load_project_instructions(package, max_parents=3)

    assert "monorepo rules" in text and "package rules" in text
    # Nearest instructions come last, so they win in the model's reading order.
    assert text.index("monorepo rules") < text.index("package rules")
    assert str(repo / "AGENTS.md") in text


def test_parent_walk_is_bounded_and_can_be_disabled(tmp_path: Path, monkeypatch):
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    _write(parent / "AGENTS.md", "parent rules")

    monkeypatch.delenv("BRAHMA_AGENT_INSTRUCTION_PARENTS", raising=False)
    assert "parent rules" in load_project_instructions(child, max_parents=1)
    assert "parent rules" not in load_project_instructions(child, max_parents=0)

    monkeypatch.setenv("BRAHMA_AGENT_INSTRUCTION_PARENTS", "0")
    assert "parent rules" not in load_project_instructions(child)

    monkeypatch.setenv("BRAHMA_AGENT_INSTRUCTION_PARENTS", "not-a-number")
    assert "parent rules" in load_project_instructions(child, max_parents=1)


def test_filesystem_root_is_never_walked(tmp_path: Path):
    dirs = instruction_dirs(tmp_path, max_parents=10)
    assert dirs[-1] == tmp_path.resolve()
    assert dirs[0] != Path(dirs[0].anchor)
    assert len(dirs) <= 11


def test_truncation_keeps_the_most_specific_instructions(tmp_path: Path):
    _write(tmp_path / "AGENTS.md", "old" * MAX_PROJECT_CHARS)
    _write(tmp_path / "BRAHMA.md", "NEAREST RULES")

    text = load_project_instructions(tmp_path, max_parents=0)

    assert len(text) <= MAX_PROJECT_CHARS + 60
    assert text.startswith("... [earlier project instructions truncated]")
    assert "NEAREST RULES" in text


def test_missing_and_empty_files_are_ignored(tmp_path: Path):
    _write(tmp_path / "AGENTS.md", "   \n")
    assert load_project_instructions(tmp_path, max_parents=0) == ""
    assert load_project_instructions(None) == ""


def test_system_prompt_puts_project_rules_after_generic_advice(tmp_path: Path):
    _write(tmp_path / "AGENTS.md", "PROJECT-SPECIFIC RULE")
    prompt = build_system_prompt(tmp_path, extra="EXTRA", permission_note="NOTE")

    assert prompt.index("You are Brahma") < prompt.index("NOTE")
    assert prompt.index("NOTE") < prompt.index("PROJECT-SPECIFIC RULE")
    assert prompt.index("PROJECT-SPECIFIC RULE") < prompt.index("EXTRA")
