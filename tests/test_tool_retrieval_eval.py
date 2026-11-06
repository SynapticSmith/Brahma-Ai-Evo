"""Retrieval gold set: two same-word servers must stay distinguishable.

The canonical failure is "search the vault" when both a kernel vault (Calyx)
and a notes vault (Obsidian) are connected. R0 does not try to invent a
confidence score; it makes the collision *visible* (server groups, match
reasons, an AMBIGUOUS contrast set) and refuses to execute an unqualified name
that exists on more than one server.

See docs/TOOL-RETRIEVAL-DESIGN.md §4.4, §4.5, §4.7 and §5 (Phase R0).
"""

from __future__ import annotations

import asyncio

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
    """A synthetic two-vault catalogue, independent of installed MCP servers."""
    return [
        ToolEntry(
            name="mcp_Calyx_vault_search",
            description=(
                "Search the Calyx kernel vault for a claim, constellation, or "
                "grounded memory."
            ),
            parameters=QUERY_SCHEMA,
            source="mcp:Calyx",
            domain="kernel constellation / memory vault",
            keywords="calyx vault kernel memory constellation lineage",
        ),
        ToolEntry(
            name="mcp_Calyx_kernel_answer",
            description=(
                "Answer a question from the Calyx kernel vault with citations "
                "and lineage."
            ),
            parameters=QUERY_SCHEMA,
            source="mcp:Calyx",
            domain="kernel constellation / memory vault",
            keywords="calyx kernel answer constellation lineage",
        ),
        ToolEntry(
            name="mcp_Obsidian_vault_search",
            description=(
                "Full-text search across the Obsidian notes vault, including "
                "backlinks and tags."
            ),
            parameters=QUERY_SCHEMA,
            source="mcp:Obsidian",
            domain="notes vault",
            keywords="obsidian notes vault markdown backlinks tags",
        ),
        ToolEntry(
            name="mcp_Obsidian_create_note",
            description=(
                "Create a new note in the Obsidian notes vault with frontmatter."
            ),
            parameters=QUERY_SCHEMA,
            source="mcp:Obsidian",
            domain="notes vault",
            keywords="obsidian notes create note markdown",
        ),
        ToolEntry(
            name="web_search",
            description="Search the public web for facts, prices, and current information.",
            parameters=QUERY_SCHEMA,
            source="native",
            domain="web",
            keywords="web search internet facts current",
        ),
    ]


@pytest.fixture
def vaults(tmp_path, monkeypatch):
    """An isolated index with both vaults indexed and a recording executor."""
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
    index.put_manual(ti.SkillManual(skill_id="mcp:Calyx", title="Calyx", body="Calyx rules."))
    index.put_manual(
        ti.SkillManual(skill_id="mcp:Obsidian", title="Obsidian", body="Obsidian rules.")
    )
    index.rebuild()
    yield index, calls
    tool_bridge.configure(settings={"tool_index_enabled": True}, executor=None)
    tool_bridge.reset_session_learning()
    ti.reset_index()


# --------------------------------------------------------------------------- #
# Presentation: groups, reasons, ambiguity
# --------------------------------------------------------------------------- #


def test_short_shared_noun_reports_both_servers_and_an_ambiguity_block(vaults):
    out = asyncio.run(tool_bridge.execute_meta("search_tools", {"intent": "vault"}))

    assert "AMBIGUOUS" in out
    assert "── Calyx ──" in out
    assert "── Obsidian ──" in out
    assert "matched:" in out
    assert "Refine with search_tools(intent='vault'" in out
    # Both qualified names are visible; the model is never handed a bare one.
    assert "mcp_Calyx_vault_search" in out
    assert "mcp_Obsidian_vault_search" in out
    assert "manual: get_skill_manual('mcp:Calyx')" in out


def test_server_filter_is_applied_exactly_and_is_not_called_ambiguous(vaults):
    out = asyncio.run(
        tool_bridge.execute_meta(
            "search_tools", {"intent": "vault", "server": "Calyx"}
        )
    )

    assert "mcp_Calyx_vault_search" in out
    assert "mcp_Obsidian_vault_search" not in out
    assert "server='Calyx'" in out
    assert "AMBIGUOUS" not in out


def test_domain_filter_narrows_without_guessing(vaults):
    out = asyncio.run(
        tool_bridge.execute_meta("search_tools", {"intent": "note", "domain": "notes"})
    )

    assert "mcp_Obsidian_create_note" in out
    assert "mcp_Calyx" not in out


def test_browse_mode_lists_one_server_without_an_intent(vaults):
    out = asyncio.run(tool_bridge.execute_meta("search_tools", {"server": "Obsidian"}))

    assert not out.startswith("Error:")
    assert "mcp_Obsidian_vault_search" in out
    assert "mcp_Obsidian_create_note" in out
    assert "mcp_Calyx" not in out


def test_unknown_filter_returns_an_honest_empty_result(vaults):
    out = asyncio.run(
        tool_bridge.execute_meta("search_tools", {"intent": "vault", "server": "Nope"})
    )

    assert "No tool matches" in out
    assert "filter is applied exactly" in out


def test_schema_detail_can_be_requested(vaults):
    out = asyncio.run(
        tool_bridge.execute_meta(
            "search_tools",
            {"intent": "vault", "server": "Calyx", "detail": "schema"},
        )
    )

    assert "schema:" in out
    assert '"query"' in out


# --------------------------------------------------------------------------- #
# Execution guard: never guess between servers
# --------------------------------------------------------------------------- #


def test_bare_name_shared_by_two_servers_is_refused(vaults):
    _index, calls = vaults
    out = asyncio.run(
        tool_bridge.execute_meta("call_tool", {"tool_name": "vault_search"})
    )

    assert out.startswith("Error:")
    assert "ambiguous" in out
    assert "mcp_Calyx_vault_search" in out
    assert "mcp_Obsidian_vault_search" in out
    assert calls == [], "an ambiguous name must never execute"


def test_exact_qualified_name_still_executes(vaults):
    _index, calls = vaults
    out = asyncio.run(
        tool_bridge.execute_meta(
            "call_tool",
            {
                "tool_name": "mcp_Calyx_vault_search",
                "arguments": {"query": "lineage"},
            },
        )
    )

    assert "executed mcp_Calyx_vault_search" in out
    assert calls == [("mcp_Calyx_vault_search", {"query": "lineage"})]


def test_same_name_from_two_servers_cannot_be_called_silently(vaults):
    index, calls = vaults
    index.upsert(ToolEntry(name="shared_tool", description="Calyx copy.", source="mcp:Calyx"))
    index.upsert(
        ToolEntry(name="shared_tool", description="Obsidian copy.", source="mcp:Obsidian")
    )

    out = asyncio.run(
        tool_bridge.execute_meta("call_tool", {"tool_name": "shared_tool"})
    )

    assert out.startswith("Error:")
    assert "more than one server" in out
    assert "mcp:Calyx" in out and "mcp:Obsidian" in out
    assert calls == [], "the later registration must not win silently"


# --------------------------------------------------------------------------- #
# Gold-set metrics
# --------------------------------------------------------------------------- #

#: (intent, context, expected tool, expect_ambiguous)
GOLD = [
    (
        "vault",
        "search my Obsidian notes vault for the Q3 plan",
        "mcp_Obsidian_vault_search",
        False,
    ),
    ("vault", "answer from the Calyx kernel vault", "mcp_Calyx_vault_search", False),
    ("vault", "", None, True),
    (
        "create a note",
        "add a note to my Obsidian vault",
        "mcp_Obsidian_create_note",
        False,
    ),
    (
        "kernel constellation",
        "show me the Calyx lineage",
        "mcp_Calyx_kernel_answer",
        False,
    ),
    ("mcp_Calyx_vault_search", "", "mcp_Calyx_vault_search", False),
    ("web search", "look up the current price of NVDA", "web_search", False),
]


def _bare(name: str, entry) -> str:
    server = entry.server.lower()
    lowered = name.lower()
    for prefix in (f"mcp_{server}_", f"{server}_", "mcp_"):
        if lowered.startswith(prefix):
            return lowered[len(prefix):]
    return lowered


def test_gold_set_meets_the_r0_thresholds(vaults):
    index, _calls = vaults
    rows = []
    for intent, context, expected, expect_ambiguous in GOLD:
        hits = index.search(intent, k=3, context=context)
        names = [h.name for h in hits]
        ambiguity = index.ambiguity(
            index.search(intent, k=6, context=context),
            " ".join(t for t in (intent, context) if t),
        )
        rows.append(
            {
                "intent": intent,
                "context": context,
                "names": names,
                "expected": expected,
                "ambiguous": bool(ambiguity),
                "expect_ambiguous": expect_ambiguous,
                "expected_entry": index.get(expected) if expected else None,
            }
        )

    ranked = [r for r in rows if r["expected"]]
    recall_at_1 = sum(1 for r in ranked if r["names"][:1] == [r["expected"]]) / len(ranked)
    recall_at_3 = sum(1 for r in ranked if r["expected"] in r["names"][:3]) / len(ranked)
    mrr = sum(
        1.0 / (r["names"].index(r["expected"]) + 1) if r["expected"] in r["names"] else 0.0
        for r in ranked
    ) / len(ranked)

    confusions = 0
    for row in ranked:
        expected = row["expected_entry"]
        if expected is None:
            continue
        bare = _bare(expected.name, expected)
        for name in row["names"]:
            if name == row["expected"]:
                break
            other = index.get(name)
            if other is not None and other.server != expected.server and _bare(name, other) == bare:
                confusions += 1
                break
    confusion_rate = confusions / len(ranked)

    expected_ambiguous = [r["expect_ambiguous"] for r in rows]
    flagged = [r["ambiguous"] for r in rows]
    true_positive = sum(1 for e, f in zip(expected_ambiguous, flagged) if e and f)
    ambiguity_recall = true_positive / max(1, sum(expected_ambiguous))
    ambiguity_precision = true_positive / max(1, sum(flagged))

    assert recall_at_1 >= 0.75, rows
    assert recall_at_3 >= 0.90, rows
    assert mrr >= 0.80, rows
    assert confusion_rate <= 0.05, rows
    assert ambiguity_precision >= 0.80, rows
    assert ambiguity_recall >= 0.90, rows


def test_naming_the_server_wins_at_every_k(vaults):
    """R1's acceptance, satisfied by the index we already have.

    Every MCP tool is indexed under ``mcp_<server>_<tool>`` and
    ``ToolEntry.search_text`` repeats the name variants three times, so the
    owner is weighted more strongly than a separate bm25 column would weight
    it. Naming the server must put that owner first and keep it above every
    tool from a same-word server, at every k.
    """
    index, _calls = vaults

    for server, other in (("Calyx", "Obsidian"), ("Obsidian", "Calyx")):
        for k in (1, 2, 3):
            hits = index.search(f"{server.lower()} vault", k=k)
            assert hits, (server, k)
            servers = [hit.entry.server for hit in hits]
            assert servers[0] == server, (server, k, [h.name for h in hits])
            if other in servers:
                assert servers.index(server) < servers.index(other), (
                    server,
                    k,
                    [h.name for h in hits],
                )
