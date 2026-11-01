"""Unit tests for :mod:`core.skills_library`.

The library exists to hold tens of thousands of skills while almost none of them
are resident, so the assertions that matter most are:

* the catalogue is built from metadata only — bodies are materialised on
  ``get``/``load``, never during the scan;
* a skill is discoverable but inert until it is explicitly loaded;
* executable skills run, and publish into the registry as ordinary tools;
* a malformed or hostile skill can never break the library.
"""

from pathlib import Path

import pytest

from core import skills_library as sl


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def lib(tmp_path):
    """An isolated library with separate builtin and user roots."""
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    return sl.SkillsLibrary(builtin, user)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _skill(lib: sl.SkillsLibrary, source: str, name: str, body: str) -> Path:
    """Create a ``<source>/<name>/skill.yaml`` and return its path."""
    root = lib.builtin_dir if source == "builtin" else lib.user_dir
    return _write(root / name / "skill.yaml", body)


class FakeRegistry:
    """Just enough of ``DynamicToolRegistry`` for the publish/execute paths."""

    def __init__(self):
        self.tools = {}

    def register_external_tool(self, name, description="", parameters=None, executor=None, *, version="1.0.0",
                               active=True, source="external"):
        self.tools[name] = {"description": description, "parameters": parameters, "executor": executor,
                            "source": source, "version": version}
        return object()

    def unregister_external_tool(self, name):
        return self.tools.pop(name, None) is not None

    def get_tool_declarations(self):
        return [{"name": n, "description": v["description"], "parameters": v["parameters"]}
                for n, v in self.tools.items()]

    def get_skill(self, name):
        if name in self.tools:
            return type("S", (), {"manifest": {"source": self.tools[name]["source"]}})()
        return None


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_discovers_yaml_skill_directory(lib):
    _skill(lib, "builtin", "web-research", "id: web-research\nname: Web Research\n"
           "description: rigorous research\ncategory: research\ntags: [web, sources]\n"
           "instructions: |\n  Do the thing.\n")
    lib.refresh(force=True)
    assert [m.skill_id for m in lib.entries()] == ["web-research"]
    meta = lib.meta("web-research")
    assert meta.name == "Web Research"
    assert meta.category == "research"
    assert meta.tags == ["web", "sources"]
    assert meta.executable is False


def test_discovers_markdown_frontmatter_and_body_instructions(lib):
    _write(lib.builtin_dir / "rigor" / "SKILL.md",
           "---\nid: rigor\nname: Rigor\ndescription: think carefully\n---\n\nStep one.\nStep two.\n")
    lib.refresh(force=True)
    spec = lib.get("rigor")
    assert spec is not None
    assert "Step one." in spec.instructions
    assert spec.name == "Rigor"


def test_markdown_without_frontmatter_is_instructions(lib):
    _write(lib.builtin_dir / "plain.md", "Just do the sensible thing.\n")
    lib.refresh(force=True)
    spec = lib.get("plain")
    assert spec is not None
    assert spec.instructions == "Just do the sensible thing."


def test_discovers_single_file_and_json_skills(lib):
    _write(lib.builtin_dir / "notes.json",
           '{"name": "Notes", "description": "take notes", "instructions": "be terse"}')
    _write(lib.builtin_dir / "summarise.md",
           "---\nid: summarize\ndescription: shorten text\n---\nSummarise.\n")
    lib.refresh(force=True)
    ids = {m.skill_id for m in lib.entries()}
    assert ids == {"notes", "summarize"}
    assert lib.get("summarize").instructions == "Summarise."
    assert lib.get("notes").instructions == "be terse"


def test_readme_and_index_files_are_not_skills(lib):
    _write(lib.builtin_dir / "README.md", "# not a skill\n")
    _write(lib.builtin_dir / "index.md", "not a skill either\n")
    _skill(lib, "builtin", "real", "id: real\ninstructions: yes\n")
    lib.refresh(force=True)
    assert [m.skill_id for m in lib.entries()] == ["real"]


def test_malformed_skill_is_skipped_not_fatal(lib):
    _write(lib.builtin_dir / "broken" / "skill.yaml", "id: [unterminated\n  bad: :\n")
    _skill(lib, "builtin", "good", "id: good\ninstructions: fine\n")
    stats = lib.refresh(force=True)
    assert "good" in {m.skill_id for m in lib.entries()}
    assert stats.skills == 1


# --------------------------------------------------------------------------- #
# Laziness and caching
# --------------------------------------------------------------------------- #


def test_catalogue_is_metadata_only_until_loaded(lib):
    _skill(lib, "builtin", "alpha",
           "id: alpha\ndescription: alpha skill\ninstructions: |\n  " + ("long " * 500) + "\n")
    lib.refresh(force=True)
    meta = lib.entries()[0]
    # The resident catalogue row carries no body at all.
    assert not hasattr(meta, "instructions")
    assert not hasattr(meta, "manual")
    # The body appears only once it is asked for.
    spec = lib.get("alpha")
    assert spec is not None
    assert spec.instructions.startswith("long long")
    # ...and is then cached.
    assert lib.get("alpha") is spec


def test_get_reloads_when_the_file_changes(lib):
    path = _skill(lib, "builtin", "alpha", "id: alpha\ninstructions: first\n")
    lib.refresh(force=True)
    first = lib.get("alpha")
    assert first.instructions == "first"
    _write(path, "id: alpha\ninstructions: second\n")
    lib.refresh(force=True)
    second = lib.get("alpha")
    assert second.instructions == "second"


def test_scan_does_not_import_executor_modules(lib, monkeypatch):
    _skill(lib, "builtin", "exec", "id: exec\nexecutable: true\n")
    _write(lib.builtin_dir / "exec" / "executor.py", "raise RuntimeError('must not be imported at scan time')\n")
    lib.refresh(force=True)  # would raise if the executor were imported here
    assert lib.meta("exec").executable is True


# --------------------------------------------------------------------------- #
# Override, activation and toggling
# --------------------------------------------------------------------------- #


def test_user_root_overrides_builtin_on_id_collision(lib):
    _skill(lib, "builtin", "shared", "id: shared\ndescription: builtin version\ninstructions: builtin\n")
    _skill(lib, "user", "shared", "id: shared\ndescription: user version\ninstructions: user\n")
    lib.refresh(force=True)
    lib.refresh(force=True)
    assert len(lib.entries()) == 1
    meta = lib.meta("shared")
    assert meta.source == "user"
    assert lib.get("shared").instructions == "user"


def test_active_false_is_unavailable(lib):
    _skill(lib, "builtin", "off", "id: off\nactive: false\ninstructions: x\n")
    lib.refresh(force=True)
    assert lib.has("off")
    assert lib.get("off", include_disabled=False) is None
    assert lib.search("off") == []
    assert lib.get("off").instructions == "x"  # still reachable by id


def test_disable_and_enable(lib):
    _skill(lib, "builtin", "one", "id: one\ninstructions: a\n")
    lib.refresh(force=True)
    lib.disable("one")
    assert lib.get("one", include_disabled=False) is None
    lib.enable("one")
    assert lib.get("one", include_disabled=False) is not None


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


def test_search_ranks_relevant_skill_first(lib):
    _skill(lib, "builtin", "web-research", "id: web-research\nname: Web Research\n"
           "description: plan and run a rigorous web research pass\n"
           "tags: [web, research, sources]\ncategory: research\ninstructions: x\n")
    _skill(lib, "builtin", "word-count", "id: word-count\nname: Word Count\n"
           "description: count words in a block of text\ncategory: text\ninstructions: x\n")
    lib.refresh(force=True)
    hits = lib.search("research a topic on the web using primary sources")
    assert hits[0].skill_id == "web-research"


def test_search_exact_id_is_boosted(lib):
    _skill(lib, "builtin", "deploy", "id: deploy\ndescription: ship code\ninstructions: x\n")
    _skill(lib, "builtin", "deployment-guide", "id: deployment-guide\ndescription: how to ship\ninstructions: x\n")
    lib.refresh(force=True)
    assert lib.search("deploy")[0].skill_id == "deploy"


def test_search_executable_only(lib):
    _skill(lib, "builtin", "talk", "id: talk\ndescription: say something useful\ninstructions: x\n")
    _skill(lib, "builtin", "run", "id: run\nexecutable: true\ndescription: run something useful\ninstructions: x\n")
    _write(lib.builtin_dir / "run" / "executor.py", "def execute(**k):\n    return 'ok'\n")
    lib.refresh(force=True)
    hits = lib.search("something useful", executable_only=True)
    assert [h.skill_id for h in hits] == ["run"]


def test_search_empty_query_returns_nothing(lib):
    _skill(lib, "builtin", "x", "id: x\ninstructions: x\n")
    lib.refresh(force=True)
    assert lib.search("") == []


# --------------------------------------------------------------------------- #
# Executable skills
# --------------------------------------------------------------------------- #


def test_executable_skill_runs(lib):
    _skill(lib, "builtin", "word-count", "id: word-count\nexecutable: true\ninstructions: x\n")
    _write(lib.builtin_dir / "word-count" / "executor.py",
           "def execute(text='', **kwargs):\n    return {'words': len(text.split())}\n")
    lib.refresh(force=True)
    assert lib.execute_skill("word-count", text="one two three") == {"words": 3}


def test_async_executor_is_awaited(lib):
    _skill(lib, "builtin", "async-skill", "id: async-skill\nexecutable: true\ninstructions: x\n")
    _write(lib.builtin_dir / "async-skill" / "executor.py",
           "import asyncio\n\nasync def execute(**kwargs):\n    await asyncio.sleep(0)\n    return 'done'\n")
    lib.refresh(force=True)
    assert lib.execute_skill("async-skill") == "done"


def test_executable_without_executor_falls_back_to_instructional(lib):
    _skill(lib, "builtin", "claims-exec", "id: claims-exec\nexecutable: true\ninstructions: x\n")
    lib.refresh(force=True)
    assert lib.meta("claims-exec").executable is False


# --------------------------------------------------------------------------- #
# Publishing
# --------------------------------------------------------------------------- #


def test_publish_registers_external_tools(lib):
    _skill(lib, "builtin", "word-count", "id: word-count\nexecutable: true\ntool_name: word_count\n"
           "description: count words\ninstructions: x\n")
    _write(lib.builtin_dir / "word-count" / "executor.py",
           "def execute(text='', **kwargs):\n    return {'words': len(text.split())}\n")
    _skill(lib, "builtin", "guidance", "id: guidance\ndescription: pure context\ninstructions: x\n")
    lib.refresh(force=True)

    registry = FakeRegistry()
    published = lib.publish(registry)
    assert published == 1
    assert "word_count" in registry.tools
    assert registry.tools["word_count"]["source"] == sl.SKILL_SOURCE
    assert registry.tools["word_count"]["executor"](text="a b") == {"words": 2}
    # Instructional skills are never published as tools.
    assert "guidance" not in registry.tools

    # Disabling the skill un-publishes it on the next sync.
    lib.disable("word-count")
    assert lib.publish(registry) == 0
    assert "word_count" not in registry.tools


def test_publish_is_idempotent(lib):
    _skill(lib, "builtin", "e", "id: e\nexecutable: true\ninstructions: x\n")
    _write(lib.builtin_dir / "e" / "executor.py", "def execute(**k):\n    return 1\n")
    lib.refresh(force=True)
    registry = FakeRegistry()
    assert lib.publish(registry) == 1
    assert lib.publish(registry) == 1
    assert len(registry.tools) == 1


# --------------------------------------------------------------------------- #
# Scale and misc
# --------------------------------------------------------------------------- #


def test_scale_thousands_of_skills_stay_searchable(lib):
    for i in range(400):
        _write(lib.builtin_dir / f"skill-{i:04d}" / "skill.yaml",
               f"id: skill-{i:04d}\ndescription: capability number {i} for widget handling\n"
               f"category: bulk\ntags: [bulk, widget]\ninstructions: body {i}\n")
    stats = lib.refresh(force=True)
    assert stats.skills == 400
    hits = lib.search("widget capability 250", limit=5)
    assert hits, "a large catalogue must still return hits"
    assert any(h.skill_id.startswith("skill-") for h in hits)
    # The catalogue rows carry no bodies even at scale.
    assert not any(hasattr(m, "instructions") for m in lib.entries())


def test_sanitize_identifier():
    assert sl.sanitize_identifier("word-count") == "word_count"
    assert sl.sanitize_identifier("My Skill!") == "My_Skill"
    assert sl.sanitize_identifier("3 things") == "skill_3_things"
    assert sl.sanitize_identifier("") == "skill"


def test_configure_library_repoints_singleton(tmp_path):
    sl.reset_library()
    try:
        builtin = tmp_path / "b"
        user = tmp_path / "u"
        builtin.mkdir()
        user.mkdir()
        _write(builtin / "s" / "skill.yaml", "id: s\ninstructions: x\n")
        lib = sl.configure_library(builtin_dir=builtin, user_dir=user)
        assert lib.get("s") is not None
        assert sl.get_library() is lib
    finally:
        sl.reset_library()


# --------------------------------------------------------------------------- #
# Project roots (`.brahma/skills`, D42)
# --------------------------------------------------------------------------- #


def _project_skill(root: Path, skill_id: str, *, description: str = "project skill",
                   frontmatter: str = "") -> Path:
    """Write the harness contract: ``<root>/<id>/SKILL.md``."""
    return _write(
        root / skill_id / "SKILL.md",
        f"---\nname: {skill_id}\ndescription: {description}\n{frontmatter}---\n\nDo it.\n",
    )


def _project_lib(tmp_path: Path, project_dirs) -> sl.SkillsLibrary:
    """An isolated library whose only skills are the ones under test."""
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir(exist_ok=True)
    user.mkdir(exist_ok=True)
    return sl.SkillsLibrary(builtin, user, project_dirs=project_dirs)


def test_project_root_is_catalogued_and_loadable(tmp_path):
    root = tmp_path / ".brahma" / "skills"
    _project_skill(root, "release-notes", description="write the changelog")

    lib = _project_lib(tmp_path, [root])
    stats = lib.refresh()

    meta = lib.meta("release-notes")
    assert meta is not None
    assert meta.source == sl.PROJECT_SOURCE
    assert stats.project == 1
    assert lib.manifest()["project"] == 1
    assert ("project", root) in lib.roots()
    spec = lib.get("release-notes")
    assert spec is not None
    assert "Do it." in spec.instructions
    assert [hit.skill_id for hit in lib.search("changelog")] == ["release-notes"]


def test_project_skill_overrides_builtin_and_user_copies(tmp_path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    project = tmp_path / "project"
    _write(builtin / "review" / "skill.yaml", "id: review\ndescription: builtin copy\ninstructions: b\n")
    _write(user / "review" / "skill.yaml", "id: review\ndescription: user copy\ninstructions: u\n")
    _project_skill(project, "review", description="project copy")

    lib = sl.SkillsLibrary(builtin, user, project_dirs=[project])
    lib.refresh()

    meta = lib.meta("review")
    assert meta.source == sl.PROJECT_SOURCE
    assert meta.description == "project copy"
    assert lib.get("review").instructions.strip() == "Do it."
    assert lib.stats().project == 1


def test_the_nearest_project_root_wins(tmp_path):
    far = tmp_path / "repo" / ".brahma" / "skills"
    near = tmp_path / "repo" / "pkg" / ".brahma" / "skills"
    _project_skill(far, "style", description="repo style")
    _project_skill(near, "style", description="package style")

    # Library order is outermost first, so the nearest root comes last.
    lib = sl.SkillsLibrary(project_dirs=[far, near])
    lib.refresh()

    assert lib.meta("style").description == "package style"


def test_project_skills_are_instructional_even_with_an_executor(tmp_path):
    root = tmp_path / ".brahma" / "skills"
    _project_skill(
        root,
        "deploy",
        frontmatter="executable: true\ntool_name: deploy_now\nactive: false\nmanual: hidden\n",
    )
    _write(root / "deploy" / "executor.py", "def execute(**k):\n    raise AssertionError('must not run')\n")

    lib = _project_lib(tmp_path, [root])
    lib.refresh()
    registry = FakeRegistry()
    assert lib.publish(registry) == 0

    spec = lib.get("deploy")
    assert spec.executable is False
    assert spec.tool_name == ""
    assert spec.active is True
    assert spec.manual == ""
    assert lib.meta("deploy").active is True
    assert spec.resolved_tool_name() != "deploy_now"
    assert "[CALLABLE]" not in spec.rendered()


def test_project_skill_name_mismatch_is_skipped(tmp_path):
    root = tmp_path / ".brahma" / "skills"
    _write(root / "one" / "SKILL.md", "---\nname: two\ndescription: wrong\n---\nbody\n")
    _project_skill(root, "good")

    lib = _project_lib(tmp_path, [root])
    lib.refresh()

    assert lib.meta("one") is None
    assert lib.meta("good") is not None


def test_project_skill_derives_a_description_from_the_body(tmp_path):
    root = tmp_path / ".brahma" / "skills"
    _write(root / "plain" / "SKILL.md", "---\nname: plain\n---\n# Plain rules\n\nKeep it short.\n")

    lib = _project_lib(tmp_path, [root])
    lib.refresh()

    assert lib.meta("plain").description == "Plain rules"


def test_invalid_project_skill_directory_name_is_skipped(tmp_path):
    root = tmp_path / ".brahma" / "skills"
    _write(root / "Bad_Name" / "SKILL.md", "---\nname: Bad_Name\n---\nbody\n")

    lib = _project_lib(tmp_path, [root])
    lib.refresh()

    assert lib.entries() == []


def test_project_roots_accept_only_the_harness_contract(tmp_path):
    root = tmp_path / ".brahma" / "skills"
    # A loose single-file skill and a YAML-only directory are both invisible to
    # agent.skills.discover, so the library must not list them either.
    _write(root / "loose.md", "do something\n")
    _write(root / "yaml-only" / "skill.yaml", "id: yaml-only\ninstructions: x\n")
    _project_skill(root, "real")

    lib = _project_lib(tmp_path, [root])
    lib.refresh()

    assert [meta.skill_id for meta in lib.entries()] == ["real"]


def test_configure_library_follows_the_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAHMA_SKILLS_DIR", raising=False)
    repo = tmp_path / "repo"
    package = repo / "packages" / "pkg"
    package.mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    _write(package / ".brahma" / "skills" / "from-pkg" / "SKILL.md",
           "---\nname: from-pkg\n---\nPackage rule.\n")
    _write(repo / ".brahma" / "skills" / "from-repo" / "SKILL.md",
           "---\nname: from-repo\n---\nRepo rule.\n")
    _write(other / ".brahma" / "skills" / "from-other" / "SKILL.md",
           "---\nname: from-other\n---\nOther rule.\n")

    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    sl.reset_library()
    try:
        lib = sl.configure_library(
            builtin_dir=builtin, user_dir=user, project_workspace=str(package)
        )
        assert lib.project_dirs()[-1] == (package.resolve() / ".brahma" / "skills")
        assert lib.has("from-pkg")
        assert lib.has("from-repo")

        sl.configure_library(project_workspace=str(other))
        assert lib.project_dirs()[-1] == (other.resolve() / ".brahma" / "skills")
        assert lib.has("from-other")
        assert not lib.has("from-pkg")
    finally:
        sl.reset_library()


def test_project_skill_name_pattern_matches_the_harness():
    from agent import skills as agent_skills

    assert sl.PROJECT_SKILL_NAME_RE.pattern == agent_skills.NAME_RE.pattern
