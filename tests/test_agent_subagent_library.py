"""The subagent template library: an index, not 600 prompt bodies.

Pure Python on purpose: harness CI installs only pytest. Most tests build their
own temp library so they never depend on the user's vault; the port tests at the
bottom prove the repository's vendored copy is present and usable on its own.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import subagent_library
from agent.loop import AgentLoop
from agent.permissions import PermissionPolicy
from agent.prompts import build_system_prompt
from agent.runtime import AgentRuntime
from agent.spec import get as get_spec
from agent.subagent import SUBAGENT_TOOL

_ROWS = {
    "code-reviewer": {
        "category": "development",
        "description": (
            "Expert code review specialist for quality, security and performance."
        ),
        "body": "CODE REVIEW TEMPLATE BODY\nLook for bugs before style.",
    },
    "code-reviewer-lite": {
        "category": "development",
        "description": "A lightweight second-pass code review.",
        "body": "LITE TEMPLATE BODY",
    },
    "seo-strategist": {
        "category": "business",
        "description": "Search engine optimisation strategy and keyword planning.",
        "body": "SEO TEMPLATE BODY",
    },
}


def _write_library(root: Path) -> Path:
    (root / "agents").mkdir(parents=True)
    rows = []
    for slug, spec in _ROWS.items():
        rel = f"agents/{slug}.md"
        text = (
            f'---\ndescription: "{spec["description"]}"\nmode: subagent\n---\n\n'
            f'{spec["body"]}\n'
        )
        (root / rel).write_text(text, encoding="utf-8")
        rows.append(
            {
                "name": slug,
                "slug": slug,
                "kind": "agent",
                "category": spec["category"],
                "category_score": 5,
                "description": spec["description"],
                "tools": ["Read"],
                "bytes": len(text.encode("utf-8")),
                "sha256": "",
                "path": rel,
            }
        )
    readme = "---\ndescription: Library readme\n---\n\nREADME BODY\n"
    (root / "agents" / "README.md").write_text(readme, encoding="utf-8")
    rows.append(
        {
            "name": "README",
            "slug": "README",
            "kind": "doc",
            "category": "ai_ml",
            "category_score": 1,
            "description": "Library readme",
            "tools": [],
            "bytes": len(readme.encode("utf-8")),
            "sha256": "",
            "path": "agents/README.md",
        }
    )
    (root / "catalog.json").write_text(json.dumps(rows), encoding="utf-8")
    return root


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = _write_library(tmp_path / "lib")
    subagent_library.clear_cache()
    monkeypatch.setenv(subagent_library.LIBRARY_ENV, str(root))
    yield root
    subagent_library.clear_cache()


def _runtime(tmp_path: Path) -> AgentRuntime:
    data = {
        "classes": {"read": "allow", "write": "allow", "exec": "allow", "native": "allow"},
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
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages, tools):
        self.calls.append([dict(message) for message in messages])
        return self.responses.pop(0) if self.responses else _final("done")


def _tool_output(result, call_id: str) -> str:
    for message in result.messages:
        if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
            return str(message.get("content") or "")
    raise AssertionError(f"no tool result for {call_id!r}")


def test_missing_library_degrades_to_not_installed(monkeypatch):
    subagent_library.clear_cache()
    # An explicit empty override is the "off" switch: neither the bundled tree
    # nor a user's vault may leak into a test or a deployment that pinned the
    # library off.
    monkeypatch.setenv(subagent_library.LIBRARY_ENV, "")

    assert subagent_library.load() is None
    assert subagent_library.prompt_block() == ""
    text = get_spec(subagent_library.LIBRARY_TOOL).handler({}, None)
    assert "not installed" in text
    assert SUBAGENT_TOOL in text


# --------------------------------------------------------------------------- #
# The port: the repository must carry the library, not the vault
# --------------------------------------------------------------------------- #

_REPO_LIBRARY = Path(__file__).resolve().parent.parent / subagent_library.BUNDLED_LIBRARY


def test_repository_ships_a_usable_library():
    """A plain checkout has the catalogue and every body it names."""
    subagent_library.clear_cache()
    catalog = subagent_library.load(_REPO_LIBRARY)

    assert catalog is not None
    assert catalog.root == _REPO_LIBRARY.resolve()
    assert len(catalog.agents) >= 600
    assert len(catalog.categories()) >= 5

    entry = catalog.get("code-reviewer")
    assert entry is not None
    assert catalog.template_body(entry).strip()


def test_bundled_library_is_the_default_when_nothing_overrides_it(monkeypatch):
    subagent_library.clear_cache()
    monkeypatch.delenv(subagent_library.LIBRARY_ENV, raising=False)
    monkeypatch.setattr(subagent_library, "_config_roots", lambda: [])

    assert subagent_library.library_root() == _REPO_LIBRARY.resolve()
    assert subagent_library.load() is not None


def test_env_override_wins_over_the_bundled_library(tmp_path, monkeypatch):
    custom = _write_library(tmp_path / "custom")
    subagent_library.clear_cache()
    monkeypatch.setenv(subagent_library.LIBRARY_ENV, str(custom))
    monkeypatch.setattr(subagent_library, "_config_roots", lambda: [])

    assert subagent_library.library_root() == custom.resolve()
    assert len(subagent_library.load().agents) == 3


def test_config_override_wins_over_the_bundled_library(tmp_path, monkeypatch):
    custom = _write_library(tmp_path / "custom")
    config = tmp_path / "subagents.json"
    config.write_text(json.dumps({"library": str(custom)}), encoding="utf-8")
    subagent_library.clear_cache()
    monkeypatch.delenv(subagent_library.LIBRARY_ENV, raising=False)
    monkeypatch.setattr(subagent_library, "_config_roots", lambda: [config])

    assert subagent_library.library_root() == custom.resolve()
    assert len(subagent_library.load().agents) == 3


def test_prompt_block_is_a_category_index_not_a_body(library):
    catalog = subagent_library.load()
    assert catalog is not None
    block = catalog.prompt_block()

    assert "## Subagent library" in block
    assert "development (2)" in block
    assert "`code-reviewer`" in block
    # The index is the whole resident cost; no template body may leak in.
    assert "CODE REVIEW TEMPLATE BODY" not in block
    assert len(block) <= subagent_library.MAX_PROMPT_CHARS
    # Doc rows are not agents and do not inflate a category count.
    assert len(catalog.agents) == 3


def test_prompt_block_is_voice_honest_when_dispatch_is_unavailable(library):
    catalog = subagent_library.load()

    block = catalog.prompt_block(dispatch_available=False)

    assert "## Subagent library" in block
    # It must still name the lookup, or a voice session cannot ask about it...
    assert f"`{subagent_library.LIBRARY_TOOL}`" in block
    # ...but it must not promise a tool this surface does not advertise.
    assert "cannot dispatch" in block
    assert "exists only in the text/CLI harness" in block
    assert "then delegate with" not in block


def test_module_prompt_block_forwards_the_surface_flag(library):
    assert "cannot dispatch" in subagent_library.prompt_block(dispatch_available=False)
    assert "cannot dispatch" not in subagent_library.prompt_block()


def test_search_matches_slug_description_and_category(library):
    catalog = subagent_library.load()

    assert catalog.search("review")[0].slug == "code-reviewer"
    assert [row.slug for row in catalog.search("keyword")] == ["seo-strategist"]
    assert [row.slug for row in catalog.search("", category="business")] == [
        "seo-strategist"
    ]
    assert catalog.search("quantum-basket-weaving") == []


def test_get_resolves_prefixes_and_refuses_ambiguity(library):
    catalog = subagent_library.load()

    assert catalog.get("code-reviewer").slug == "code-reviewer"
    assert catalog.get("Code Reviewer").slug == "code-reviewer"
    # "code" is a prefix of two templates, so guessing would be a coin flip.
    assert catalog.get("code") is None
    assert catalog.get("seo").slug == "seo-strategist"
    assert catalog.get("does-not-exist") is None


def test_template_body_drops_frontmatter_and_clamps_size(library):
    catalog = subagent_library.load()
    entry = catalog.get("code-reviewer")

    body = catalog.template_body(entry)
    assert body.startswith("CODE REVIEW TEMPLATE BODY")
    assert "mode: subagent" not in body

    clamped = catalog.template_body(entry, max_chars=40)
    assert "Template truncated" in clamped


def test_list_tool_reports_inventory_categories_and_matches(library):
    handler = get_spec(subagent_library.LIBRARY_TOOL).handler

    overview = handler({}, None)
    assert "3 templates" in overview
    assert "development (2)" in overview
    assert "run_subagent_batch" in overview

    matches = handler({"query": "review", "limit": 1}, None)
    assert "`code-reviewer` [development]" in matches
    assert "subagent_type" in matches


def test_system_prompt_carries_the_index_only_when_asked(library, tmp_path):
    with_index = build_system_prompt(tmp_path)
    without = build_system_prompt(tmp_path, include_subagents=False)

    assert "## Subagent library" in with_index
    assert "## Subagent library" not in without


def test_run_subagent_runs_the_selected_template(library, tmp_path):
    runtime = _runtime(tmp_path)
    script = Script(
        [
            _turn(
                ("c1", SUBAGENT_TOOL, {
                    "task": "review the patch",
                    "subagent_type": "code-reviewer",
                })
            ),
            _final("the patch is fine"),
            _final("parent done"),
        ]
    )

    result = AgentLoop(script, runtime=runtime, max_turns=4).run("delegate")

    child_system = script.calls[1][0]["content"]
    assert "CODE REVIEW TEMPLATE BODY" in child_system
    assert "'code-reviewer' template" in child_system
    assert "Subagent subagent-1 completed" in _tool_output(result, "c1")


def test_unknown_subagent_type_is_refused_with_suggestions(library, tmp_path):
    runtime = _runtime(tmp_path)
    script = Script(
        [
            _turn(
                ("c1", SUBAGENT_TOOL, {
                    "task": "review the patch",
                    "subagent_type": "code-reveiwer",
                })
            ),
            _final("parent done"),
        ]
    )

    result = AgentLoop(script, runtime=runtime, max_turns=4).run("delegate")

    output = _tool_output(result, "c1")
    assert "no subagent template named 'code-reveiwer'" in output
    assert "`code-reviewer`" in output
    # The model gets to retry with a suggestion: an unknown slug is an argument
    # error, not a permission denial (which would end the turn).
    assert len(script.calls) == 2
