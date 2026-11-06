"""R2: a compressed search intent is completed from the user's actual turn.

The model often reduces "search my Obsidian notes for the Q3 plan" to the single
word "vault". R0 made a collision visible; R2 stops the collision from happening
when the turn already names the answer. The context is memory-only and an
explicit ``context`` argument always wins (TOOL-RETRIEVAL-DESIGN §4.1).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from core import tool_bridge
from core import tool_index as ti
from core.tool_index import ToolEntry

QUERY_SCHEMA = {
    "type": "OBJECT",
    "properties": {"query": {"type": "STRING"}},
    "required": ["query"],
}


def _catalogue() -> list[ToolEntry]:
    """Two servers that both answer to "vault", plus one unrelated tool."""
    return [
        ToolEntry(
            name="mcp_Calyx_vault_search",
            description="Search the Calyx kernel vault for claims and lineage.",
            parameters=QUERY_SCHEMA,
            source="mcp:Calyx",
            domain="kernel constellation / memory vault",
            keywords="calyx vault kernel memory constellation lineage",
        ),
        ToolEntry(
            name="mcp_Calyx_kernel_answer",
            description="Answer from the Calyx kernel with citations.",
            parameters=QUERY_SCHEMA,
            source="mcp:Calyx",
            domain="kernel constellation / memory vault",
            keywords="calyx kernel answer constellation lineage",
        ),
        ToolEntry(
            name="mcp_Obsidian_vault_search",
            description="Full-text search across the Obsidian notes vault.",
            parameters=QUERY_SCHEMA,
            source="mcp:Obsidian",
            domain="notes vault",
            keywords="obsidian notes vault markdown backlinks tags",
        ),
        ToolEntry(
            name="mcp_Obsidian_create_note",
            description="Create a note in the Obsidian notes vault.",
            parameters=QUERY_SCHEMA,
            source="mcp:Obsidian",
            domain="notes vault",
            keywords="obsidian notes create note markdown",
        ),
        ToolEntry(
            name="web_search",
            description="Search the public web for facts and prices.",
            parameters=QUERY_SCHEMA,
            source="native",
            domain="web",
            keywords="web search internet facts current",
        ),
    ]


@pytest.fixture
def vaults(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setattr(ti._EmbedBackend, "load", lambda self: False)
    ti.reset_index()
    index = ti.ToolIndex(cache_dir=tmp_path / "cache")
    monkeypatch.setattr(tool_bridge, "get_index", lambda: index)
    monkeypatch.setattr(ti, "get_index", lambda: index)

    calls: list[tuple[str, dict]] = []

    def _executor(name, args):
        calls.append((name, args))
        return f"executed {name}"

    tool_bridge.configure(
        settings={"tool_index_enabled": True, "tool_index_budget": 20},
        executor=_executor,
    )
    tool_bridge.reset_session_learning()
    for entry in _catalogue():
        index.upsert(entry)
    index.rebuild()
    yield index, calls

    tool_bridge.configure(settings={"tool_index_enabled": True}, executor=None)
    tool_bridge.reset_session_learning()
    ti.reset_index()


def _search(**args) -> str:
    return asyncio.run(tool_bridge.execute_meta("search_tools", args))


def _groups(text: str) -> list[str]:
    return re.findall(r"── (.+?) ──", text)


def test_a_short_intent_is_completed_from_the_obsidian_turn(vaults):
    tool_bridge.set_turn_context("search my Obsidian notes for the Q3 plan")

    out = _search(intent="vault")

    assert "completed from the current turn" in out
    assert _groups(out)[0] == "Obsidian"
    assert "AMBIGUOUS" not in out, "the turn already names the owner"


def test_the_same_intent_resolves_to_calyx_from_a_calyx_turn(vaults):
    tool_bridge.set_turn_context("answer from the Calyx vault with lineage")

    out = _search(intent="vault")

    assert "completed from the current turn" in out
    assert _groups(out)[0] == "Calyx"
    assert "AMBIGUOUS" not in out


def test_without_a_turn_a_bare_vault_is_still_ambiguous(vaults):
    out = _search(intent="vault")

    assert "completed from the current turn" not in out
    assert "AMBIGUOUS" in out
    assert set(_groups(out)) >= {"Calyx", "Obsidian"}


def test_an_explicit_context_beats_the_remembered_turn(vaults):
    tool_bridge.set_turn_context("answer from the Calyx vault")

    out = _search(intent="vault", context="search my Obsidian notes for the Q3 plan")

    assert _groups(out)[0] == "Obsidian"
    assert "completed from the current turn" not in out


def test_a_long_intent_that_names_the_catalogue_ignores_the_turn(vaults):
    tool_bridge.set_turn_context("search my Obsidian notes for the Q3 plan")

    out = _search(intent="look up the current stock price on the public web")

    assert "completed from the current turn" not in out
    assert _groups(out)[0] == "native"


def test_explicit_server_still_wins_over_the_turn(vaults):
    tool_bridge.set_turn_context("search my Obsidian notes for the Q3 plan")

    out = _search(intent="vault", server="Calyx")

    assert "mcp_Calyx_vault_search" in out
    assert "mcp_Obsidian_vault_search" not in out
    assert "AMBIGUOUS" not in out


def test_the_buffer_keeps_two_turns_newest_first_and_ignores_blanks(vaults):
    tool_bridge.set_turn_context("first question")
    tool_bridge.set_turn_context("   ")
    tool_bridge.set_turn_context("second question")
    tool_bridge.set_turn_context("third question")

    assert tool_bridge.turn_context() == "third question"
    assert tool_bridge.turn_context_history() == ["third question", "second question"]


def test_ending_a_session_forgets_the_turn(vaults):
    tool_bridge.set_turn_context("search my Obsidian notes")

    tool_bridge.reset_session_learning()

    assert tool_bridge.turn_context() == ""
    assert "AMBIGUOUS" in _search(intent="vault")


def test_the_agent_loop_hands_its_prompt_to_the_bridge(vaults):
    from agent.loop import _remember_turn_for_retrieval

    _remember_turn_for_retrieval("  search   my Obsidian notes  ")

    assert tool_bridge.turn_context() == "search my Obsidian notes"


def test_every_user_surface_feeds_the_bridge():
    """A seam that is only documented is a seam that rots."""
    root = Path(__file__).resolve().parent.parent
    for relative in (
        "core/text_turn_controller.py",
        "core/audio_io.py",
        "agent/loop.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "set_turn_context" in source, relative
