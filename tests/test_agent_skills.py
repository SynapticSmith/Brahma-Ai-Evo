"""Project skills: parsing, discovery, the resident list, and the loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent import skills
from agent.permissions import PermissionPolicy
from agent.prompts import build_system_prompt
from agent.runtime import AgentRuntime
from agent.spec import get as get_spec
from agent.spec import is_registered


@pytest.fixture(autouse=True)
def _isolated_roots(tmp_path: Path, monkeypatch):
    """Keep the developer's real user-level skills out of every test."""
    monkeypatch.delenv("BRAHMA_SKILLS_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))


def _skill(
    root: Path,
    name: str = "demo",
    *,
    description: str | None = "A demo skill.",
    body: str = "# Demo\n\nDo the demo thing.\n",
    extra: str = "",
    name_field: bool = True,
) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    if name_field:
        lines.append(f"name: {name}")
    if description is not None:
        lines.append(f"description: {description}")
    if extra:
        lines.append(extra)
    lines += ["---", "", body]
    (folder / skills.SKILL_FILE).write_text("\n".join(lines), encoding="utf-8")
    return folder


def _runtime(workspace: Path) -> AgentRuntime:
    return AgentRuntime(
        workspace=workspace,
        policy=PermissionPolicy.from_dict({"classes": {"read": "allow"}}),
        approver=None,
        headless=True,
    )


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def test_parse_reads_frontmatter_body_and_allowed_tools(tmp_path: Path):
    folder = _skill(tmp_path, extra="allowed-tools: [read_file, grep_files]")

    skill = skills.parse_skill(folder / skills.SKILL_FILE)

    assert skill.name == "demo"
    assert skill.description == "A demo skill."
    assert "Do the demo thing." in skill.body
    assert skill.allowed_tools == ("read_file", "grep_files")
    assert skill.directory == folder


def test_description_falls_back_to_the_first_body_heading(tmp_path: Path):
    folder = _skill(tmp_path, description=None)

    skill = skills.parse_skill(folder / skills.SKILL_FILE)

    assert skill.description == "Demo"


def test_name_falls_back_to_the_directory(tmp_path: Path):
    folder = _skill(tmp_path, name_field=False)

    skill = skills.parse_skill(folder / skills.SKILL_FILE)

    assert skill.name == "demo"


def test_invalid_names_are_rejected(tmp_path: Path):
    folder = _skill(tmp_path, name="demo", description="x")
    (folder / skills.SKILL_FILE).write_text(
        "---\nname: Bad Name\ndescription: x\n---\n", encoding="utf-8"
    )

    with pytest.raises(skills.SkillError, match="invalid skill name"):
        skills.parse_skill(folder / skills.SKILL_FILE)


def test_the_directory_and_frontmatter_name_must_match(tmp_path: Path):
    folder = _skill(tmp_path, name="demo")
    (folder / skills.SKILL_FILE).write_text(
        "---\nname: other\ndescription: x\n---\n", encoding="utf-8"
    )

    with pytest.raises(skills.SkillError, match="does not match"):
        skills.parse_skill(folder / skills.SKILL_FILE)


def test_an_oversized_file_is_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(skills, "MAX_SKILL_BYTES", 16)
    folder = _skill(tmp_path, body="# Demo\n\n" + "x" * 64)

    with pytest.raises(skills.SkillError, match="limit is 16"):
        skills.parse_skill(folder / skills.SKILL_FILE)


def test_a_missing_file_is_a_skill_error(tmp_path: Path):
    with pytest.raises(skills.SkillError, match="cannot stat"):
        skills.parse_skill(tmp_path / "nope" / skills.SKILL_FILE)


def test_a_utf8_bom_does_not_break_the_frontmatter(tmp_path: Path):
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / skills.SKILL_FILE).write_text(
        "---\nname: demo\ndescription: With a BOM\n---\n\nBody.\n",
        encoding="utf-8-sig",
    )

    skill = skills.parse_skill(folder / skills.SKILL_FILE)

    assert skill.description == "With a BOM"


def test_metadata_only_parse_leaves_the_body_empty(tmp_path: Path):
    folder = _skill(tmp_path, body="# Demo\n\n" + "x" * (skills.METADATA_BYTES * 2))

    listed = skills.parse_skill(folder / skills.SKILL_FILE, with_body=False)
    full = skills.parse_skill(folder / skills.SKILL_FILE)

    assert listed.body == ""
    assert listed.description == full.description == "A demo skill."
    assert len(full.body) > skills.METADATA_BYTES


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

def test_discovery_prefers_the_nearest_root_and_reports_the_shadow(tmp_path: Path):
    near = tmp_path / "near"
    far = tmp_path / "far"
    _skill(near, description="near copy")
    _skill(far, description="far copy")

    catalog = skills.discover(roots=[near, far])

    assert [skill.description for skill in catalog.skills] == ["near copy"]
    assert any("already defined" in item for item in catalog.diagnostics)


def test_skills_dir_override_replaces_default_discovery(tmp_path: Path, monkeypatch):
    root = tmp_path / "skills"
    _skill(root)
    monkeypatch.setenv("BRAHMA_SKILLS_DIR", str(root))

    assert skills.default_roots(tmp_path) == [root]
    assert [skill.name for skill in skills.discover(tmp_path).skills] == ["demo"]


def test_default_roots_walk_up_three_parents(tmp_path: Path):
    repo = tmp_path / "repo"
    package = repo / "packages" / "pkg"
    package.mkdir(parents=True)

    roots = skills.default_roots(package)

    assert roots[0] == package.resolve() / ".brahma" / "skills"
    assert repo.resolve() / ".brahma" / "skills" in roots
    # workspace + three parents + the user-level directory
    assert len(roots) == 5


def test_a_malformed_skill_is_a_diagnostic_not_a_failure(tmp_path: Path):
    root = tmp_path / "skills"
    _skill(root, "good")
    bad = root / "bad"
    bad.mkdir()
    (bad / skills.SKILL_FILE).write_text(
        "---\nname: Bad Name\n---\n", encoding="utf-8"
    )

    catalog = skills.discover(roots=[root])

    assert [skill.name for skill in catalog.skills] == ["good"]
    assert len(catalog.diagnostics) == 1
    assert "Bad Name" in catalog.diagnostics[0]


def test_plain_files_in_a_root_are_ignored(tmp_path: Path):
    root = tmp_path / "skills"
    root.mkdir()
    (root / "README.md").write_text("not a skill", encoding="utf-8")

    catalog = skills.discover(roots=[root])

    assert catalog.skills == ()
    assert catalog.diagnostics == ()


def test_discovery_can_skip_bodies(tmp_path: Path):
    root = tmp_path / "skills"
    _skill(root, body="# Demo\n\n" + "x" * 4_000)

    listed = skills.discover(roots=[root], with_body=False)
    full = skills.discover(roots=[root])

    assert listed.skills[0].body == ""
    assert "xxxx" in full.skills[0].body


# --------------------------------------------------------------------------- #
# Resident prompt block
# --------------------------------------------------------------------------- #

def test_prompt_block_lists_names_without_bodies(tmp_path: Path, monkeypatch):
    root = tmp_path / "skills"
    _skill(root, body="# Demo\n\nSECRET INSTRUCTION")
    monkeypatch.setenv("BRAHMA_SKILLS_DIR", str(root))

    block = skills.prompt_block(tmp_path)

    assert "## Project skills" in block
    assert "`demo`" in block
    assert "A demo skill." in block
    assert skills.LOAD_SKILL_TOOL in block
    assert "SECRET INSTRUCTION" not in block


def test_prompt_block_respects_the_skill_and_character_budgets(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "skills"
    for name in ("a-skill", "b-skill", "c-skill"):
        _skill(root, name)
    monkeypatch.setenv("BRAHMA_SKILLS_DIR", str(root))

    block = skills.prompt_block(tmp_path, max_skills=2)

    assert "`a-skill`" in block
    assert "`b-skill`" in block
    assert "`c-skill`" not in block
    assert "1 more skill(s)" in block

    tiny = skills.prompt_block(tmp_path, max_chars=40)
    assert "`a-skill`" not in tiny
    assert "3 more skill(s)" in tiny


def test_prompt_block_is_empty_when_nothing_is_installed(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("BRAHMA_SKILLS_DIR", str(tmp_path / "none"))

    assert skills.prompt_block(tmp_path) == ""


def test_system_prompt_places_skills_after_project_rules_and_before_extra(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "skills"
    _skill(root)
    monkeypatch.setenv("BRAHMA_SKILLS_DIR", str(root))
    (tmp_path / "AGENTS.md").write_text("PROJECT RULE", encoding="utf-8")

    prompt = build_system_prompt(tmp_path, extra="EXTRA", permission_note="NOTE")

    assert prompt.index("PROJECT RULE") < prompt.index("## Project skills")
    assert prompt.index("## Project skills") < prompt.index("EXTRA")


# --------------------------------------------------------------------------- #
# The loader
# --------------------------------------------------------------------------- #

def test_load_project_skill_returns_the_body_and_source(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "skills"
    folder = _skill(root, body="# Demo\n\nDo the thing.")
    monkeypatch.setenv("BRAHMA_SKILLS_DIR", str(root))

    text = _runtime(tmp_path).execute(skills.LOAD_SKILL_TOOL, {"name": "demo"})

    assert "# Skill: demo" in text
    assert "Do the thing." in text
    assert str(folder / skills.SKILL_FILE) in text


def test_load_project_skill_lists_available_names_for_an_unknown_one(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "skills"
    _skill(root, "demo")
    monkeypatch.setenv("BRAHMA_SKILLS_DIR", str(root))

    text = _runtime(tmp_path).execute(
        skills.LOAD_SKILL_TOOL, {"name": "missing"}
    )

    assert "no skill named 'missing'" in text
    assert "demo" in text


def test_load_project_skill_requires_a_name(tmp_path: Path):
    text = _runtime(tmp_path).execute(skills.LOAD_SKILL_TOOL, {})

    assert "'name' is required" in text


def test_the_harness_does_not_shadow_the_skills_library_tool():
    # ``load_skill`` belongs to core/tool_index.py and takes a ``skill_id``.
    # Registering the same name here would make core/tool_exec.py dispatch the
    # harness handler first and break the existing Skills Library.
    assert is_registered(skills.LOAD_SKILL_TOOL)
    assert not is_registered("load_skill")

    spec = get_spec(skills.LOAD_SKILL_TOOL)
    assert spec is not None
    assert spec.tool_class == "read"
    assert spec.parameters["properties"]["name"]["type"] == "string"
