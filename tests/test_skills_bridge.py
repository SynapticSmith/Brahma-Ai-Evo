"""Integration tests for the Skills Library wiring into :mod:`core.tool_bridge`.

These pin down the contract the library was built for: two always-advertised
meta-tools reach a catalogue of any size, nothing is loaded until it is asked
for, and an executable skill becomes an ordinary callable tool once loaded.
"""

import asyncio

import pytest

from core import skills_library
from core import tool_bridge
from core import tool_index as ti


class FakeRegistry:
    """Just enough of ``DynamicToolRegistry`` for publish/sync/execute."""

    def __init__(self):
        self.tools = {}

    def register_external_tool(self, name, description="", parameters=None, executor=None, *, version="1.0.0",
                               active=True, source="external"):
        self.tools[name] = {
            "description": description,
            "parameters": parameters,
            "executor": executor,
            "source": source,
            "version": version,
        }
        return object()

    def unregister_external_tool(self, name):
        return self.tools.pop(name, None) is not None

    def get_tool_declarations(self):
        return [
            {"name": n, "description": v["description"], "parameters": v["parameters"]}
            for n, v in self.tools.items()
        ]

    def get_skill(self, name):
        if name in self.tools:
            return type("S", (), {"manifest": {"source": self.tools[name]["source"]}})()
        return None


def _write_skill(root, name, body, executor=None):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "skill.yaml").write_text(body, encoding="utf-8")
    if executor is not None:
        (directory / "executor.py").write_text(executor, encoding="utf-8")


RESEARCH = (
    "id: web-research\n"
    "name: Web Research\n"
    "description: plan and run a rigorous web research pass with traceable sources\n"
    "category: research\n"
    "tags: [web, research, sources]\n"
    "instructions: |\n"
    "  Restate the request as sub-questions, then cite every source.\n"
)

WORD_COUNT = (
    "id: word-count\n"
    "name: Word Count\n"
    "executable: true\n"
    "tool_name: word_count\n"
    "description: count words in a block of text\n"
    "parameters:\n"
    "  type: OBJECT\n"
    "  properties:\n"
    "    text:\n"
    "      type: STRING\n"
    "  required: [text]\n"
    "instructions: |\n"
    "  Use this to size a block of text.\n"
)

WORD_COUNT_EXECUTOR = "def execute(text='', **kwargs):\n    return {'words': len(text.split())}\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An isolated bridge + library + index + registry.

    Yields ``(lib, builtin_dir, index, calls, registry)``.
    """
    ti.reset_index()
    idx = ti.ToolIndex(cache_dir=tmp_path / "cache")
    monkeypatch.setattr(tool_bridge, "get_index", lambda: idx)
    monkeypatch.setattr(ti, "get_index", lambda: idx)

    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    skills_library.reset_library()
    lib = skills_library.configure_library(builtin_dir=builtin, user_dir=user)

    registry = FakeRegistry()
    monkeypatch.setattr(skills_library.SkillsLibrary, "_registry", staticmethod(lambda: registry))

    calls = []

    def _executor(name, args):
        calls.append((name, args))
        return f"executed {name} with {args}"

    tool_bridge.configure(
        settings={
            "tool_index_enabled": True,
            "tool_index_budget": 20,
            "skills_library_enabled": True,
        },
        executor=_executor,
    )
    try:
        yield lib, builtin, idx, calls, registry
    finally:
        skills_library.reset_library()
        ti.reset_index()


def _run(name, args):
    return asyncio.run(tool_bridge.execute_meta(name, args))


# --------------------------------------------------------------------------- #
# Advertising
# --------------------------------------------------------------------------- #


def test_skill_meta_tools_are_advertised(env):
    lib, builtin, idx, calls, registry = env
    declarations = tool_bridge.build_session_payload("research something")["declarations"]
    names = {d["name"] for d in declarations}
    assert {"search_skills", "load_skill"} <= names
    assert tool_bridge.is_meta_tool("search_skills")
    assert tool_bridge.is_meta_tool("load_skill")


def test_they_do_not_grow_the_advertised_surface(env):
    tool_bridge.configure(settings={"tool_index_budget": 10})
    assert len(tool_bridge.build_session_payload("")["declarations"]) <= 10


# --------------------------------------------------------------------------- #
# search_skills
# --------------------------------------------------------------------------- #


def test_search_skills_returns_matches(env):
    lib, builtin, idx, calls, registry = env
    _write_skill(builtin, "web-research", RESEARCH)
    lib.refresh(force=True)

    out = _run("search_skills", {"intent": "research a topic using primary sources"})
    assert "web-research" in out
    assert "instructional" in out
    assert "load_skill" in out


def test_search_skills_requires_intent(env):
    out = _run("search_skills", {})
    assert "Error" in out
    assert "intent" in out


def test_search_skills_reports_no_match(env):
    out = _run("search_skills", {"intent": "quantum teleportation recipes"})
    assert "No skill matches" in out


def test_search_skills_reports_executable(env):
    lib, builtin, idx, calls, registry = env
    _write_skill(builtin, "word-count", WORD_COUNT, WORD_COUNT_EXECUTOR)
    lib.refresh(force=True)

    out = _run("search_skills", {"intent": "count the words in a block of text"})
    assert "word-count" in out
    assert "executable" in out
    assert "word_count" in out


def test_search_skills_executable_only(env):
    lib, builtin, idx, calls, registry = env
    _write_skill(builtin, "web-research", RESEARCH)
    _write_skill(builtin, "word-count", WORD_COUNT, WORD_COUNT_EXECUTOR)
    lib.refresh(force=True)

    out = _run("search_skills", {"intent": "research words and sources", "executable_only": True})
    assert "word-count" in out
    assert "web-research" not in out


# --------------------------------------------------------------------------- #
# load_skill
# --------------------------------------------------------------------------- #


def test_load_skill_returns_instructions(env):
    lib, builtin, idx, calls, registry = env
    _write_skill(builtin, "web-research", RESEARCH)
    lib.refresh(force=True)

    out = _run("load_skill", {"skill_id": "web-research"})
    assert "[SKILL: Web Research]" in out
    assert "cite every source" in out


def test_load_skill_requires_an_id(env):
    assert "Error" in _run("load_skill", {})


def test_load_skill_unknown_id_suggests(env):
    lib, builtin, idx, calls, registry = env
    _write_skill(builtin, "web-research", RESEARCH)
    lib.refresh(force=True)

    out = _run("load_skill", {"skill_id": "web-researchh"})
    assert "No skill named" in out
    # The nearest real id is offered rather than a bare failure.
    assert "web-research" in out


def test_disabled_skill_is_reported_not_loaded(env):
    lib, builtin, idx, calls, registry = env
    _write_skill(builtin, "web-research", RESEARCH)
    lib.refresh(force=True)
    lib.disable("web-research")

    out = _run("load_skill", {"skill_id": "web-research"})
    assert "disabled" in out.lower()


# --------------------------------------------------------------------------- #
# get_skill_manual fallback
# --------------------------------------------------------------------------- #


def test_get_skill_manual_serves_a_library_skill(env):
    lib, builtin, idx, calls, registry = env
    _write_skill(builtin, "web-research", RESEARCH)
    lib.refresh(force=True)

    out = _run("get_skill_manual", {"skill_id": "web-research"})
    assert "cite every source" in out


# --------------------------------------------------------------------------- #
# Executable skills become tools
# --------------------------------------------------------------------------- #


def test_load_skill_publishes_executable_and_makes_it_callable(env):
    lib, builtin, idx, calls, registry = env
    _write_skill(builtin, "word-count", WORD_COUNT, WORD_COUNT_EXECUTOR)
    lib.refresh(force=True)

    out = _run("load_skill", {"skill_id": "word-count"})
    assert "[CALLABLE]" in out
    assert "word_count" in out

    # Published into the registry and indexed so call_tool can reach it.
    assert "word_count" in registry.tools
    assert idx.get("word_count") is not None

    called = _run("call_tool", {"tool_name": "word_count", "arguments": {"text": "a b c"}})
    assert "executed word_count" in called
    assert calls[-1] == ("word_count", {"text": "a b c"})


def test_sync_skills_publishes_executables_only(env):
    lib, builtin, idx, calls, registry = env
    _write_skill(builtin, "web-research", RESEARCH)
    _write_skill(builtin, "word-count", WORD_COUNT, WORD_COUNT_EXECUTOR)
    lib.refresh(force=True)

    result = tool_bridge.sync_skills(registry)
    assert result["skills"] == 2
    assert result["executable"] == 1
    assert result["published"] == 1
    assert "word_count" in registry.tools
    assert "web-research" not in registry.tools
    # Published executables become ordinary indexed tools.
    assert idx.get("word_count") is not None


def test_executable_without_executor_is_not_published(env):
    lib, builtin, idx, calls, registry = env
    _write_skill(builtin, "liar", "id: liar\nexecutable: true\ndescription: claims to run\ninstructions: x\n")
    lib.refresh(force=True)

    result = tool_bridge.sync_skills(registry)
    assert result["executable"] == 0
    assert registry.tools == {}


# --------------------------------------------------------------------------- #
# Management surface
# --------------------------------------------------------------------------- #


def test_skills_catalog_groups_and_flags(env):
    lib, builtin, idx, calls, registry = env
    _write_skill(builtin, "web-research", RESEARCH)
    _write_skill(builtin, "word-count", WORD_COUNT, WORD_COUNT_EXECUTOR)
    lib.refresh(force=True)
    lib.disable("web-research")

    catalog = tool_bridge.skills_catalog()
    assert catalog["total"] == 2
    assert catalog["enabled"] is True
    assert "web-research" in catalog["disabled"]
    by_id = {s["skill_id"]: s for g in catalog["groups"] for s in g["skills"]}
    assert by_id["web-research"]["enabled"] is False
    assert by_id["word-count"]["enabled"] is True
    assert by_id["word-count"]["executable"] is True
    assert by_id["word-count"]["tool_name"] == "word_count"


def test_set_disabled_skills_round_trips(env):
    lib, builtin, idx, calls, registry = env
    _write_skill(builtin, "web-research", RESEARCH)
    lib.refresh(force=True)

    tool_bridge.set_disabled_skills(["web-research"])
    assert tool_bridge.disabled_skills() == ["web-research"]
    tool_bridge.set_disabled_skills([])
    assert tool_bridge.disabled_skills() == []


def test_turning_the_library_off_blocks_the_meta_tools(env):
    lib, builtin, idx, calls, registry = env
    _write_skill(builtin, "web-research", RESEARCH)
    lib.refresh(force=True)

    tool_bridge.set_skills_enabled(False)
    assert "switched off" in _run("search_skills", {"intent": "research"}).lower()
    assert "switched off" in _run("load_skill", {"skill_id": "web-research"}).lower()

    tool_bridge.set_skills_enabled(True)
    assert "web-research" in _run("search_skills", {"intent": "research"})


def test_snapshot_reports_the_library(env):
    lib, builtin, idx, calls, registry = env
    _write_skill(builtin, "web-research", RESEARCH)
    lib.refresh(force=True)

    snap = tool_bridge.snapshot()
    assert snap["skills"]["skills"] == 1
    assert snap["skills"]["enabled"] is True
