"""Tests for the tool & skill index engine.

The engine is the thing that keeps the Gemini Live handshake inside the 128
declaration ceiling, so the assertions that matter most are:

* the advertised payload is bounded and never exceeds the ceiling;
* every tool stays *reachable* through search + call even when unadvertised;
* retrieval degrades gracefully with zero optional dependencies installed.
"""

import pytest

from core import tool_index as ti
from core.tool_index import (
    GEMINI_MAX_TOOLS,
    META_TOOLS,
    SkillManual,
    ToolEntry,
    ToolIndex,
)
from core.tool_index import _EmbedBackend


def _entry(name, description, source="native", **kw):
    return ToolEntry(
        name=name,
        description=description,
        parameters={"type": "OBJECT", "properties": {}},
        source=source,
        **kw,
    )


@pytest.fixture
def index(tmp_path, monkeypatch):
    """A realistic catalogue: native tools plus two large MCP servers.

    Deliberately mimics the shape that produced the 130-tool crash — the sizes
    matter for the ceiling assertions.

    Semantic indexing is disabled here so assertions are deterministic and the
    suite does not pay for loading a model 30+ times. The *guaranteed* floor of
    the engine is the keyword path, so that is what the bulk of the suite pins
    down; the optional semantic backend is covered separately by
    :func:`test_semantic_backend_improves_paraphrase_routing`.
    """
    monkeypatch.setattr(ti._EmbedBackend, "load", lambda self: False)
    idx = ToolIndex(cache_dir=tmp_path / "cache")

    natives = [
        _entry("undo", "Roll back the last change made to the computer.", pinned=True),
        _entry("recall_memory", "Search long-term memory for stored facts and preferences.", pinned=True),
        _entry("web_search", "Search the public web for current information and news."),
        _entry("open_app", "Launch an installed desktop application by name."),
        _entry("system_diagnostics", "Report system health, CPU, memory and display brightness."),
        _entry("read_file", "Read a text file from the local disk."),
        _entry("set_volume", "Change the system audio output volume level."),
        # The genuine competitors to Synapse's browser/desktop tooling. These
        # are the redundancy the user hit: three built-ins that Synapse already
        # covers, which is why the count blew past 128.
        _entry("browser_control", "Open and control a Chromium browser tab: navigate, read, click, type."),
        _entry("computer_control", "Control native Windows applications via mouse and keyboard automation."),
        _entry("desktop_control", "Control the Windows desktop: launch apps, move windows, manage the desktop."),
    ]
    idx.upsert_many(natives)

    # 31 Calyx-like tools.
    calyx = [
        f"calyx_{verb}" for verb in (
            "search", "ingest", "traverse", "provenance", "abundance", "neighbors",
            "agree", "disagree", "anchor", "answer_trace", "reproduce", "kernel",
            "bits", "define", "add_lens", "profile_lens", "propose_lens", "retire_lens",
            "park_lens", "anneal", "skills", "search_skill", "guard_check", "guard_generate",
            "guard_calibrate", "list_panel", "verify_chain", "ingest_media", "measure",
            "create_vault", "insta",
        )
    ]
    idx.upsert_many(
        _entry(n, f"Calyx association-native database operation: {n.split('_')[-1]}.", source="mcp:Calyx")
        for n in calyx
    )

    # 82 Synapse-like tools, many of them near-duplicates of each other on
    # purpose: this is the description-collision hazard the index must handle.
    synapse = [
        "synapse_target_act", "synapse_observe", "synapse_find", "synapse_read_text",
        "synapse_click", "synapse_type_text", "synapse_key_press", "synapse_scroll",
        "synapse_screenshot", "synapse_launch", "synapse_browser_click",
        "synapse_browser_type", "synapse_browser_navigate", "synapse_browser_tabs",
        "synapse_browser_content", "synapse_browser_assert", "synapse_browser_screenshot",
        "synapse_cdp_open_tab", "synapse_cdp_close_tab", "synapse_cdp_navigate_tab",
        "synapse_cdp_activate_tab", "synapse_cdp_target_info", "synapse_cdp_bridge_reload",
        "synapse_browser_wait_for", "synapse_browser_wait_for_url", "synapse_browser_wait_for_selector",
        "synapse_browser_locate", "synapse_browser_inspect", "synapse_browser_frames",
        "synapse_browser_cookies", "synapse_browser_storage", "synapse_browser_downloads",
        "synapse_browser_batch", "synapse_browser_set_value", "synapse_browser_fill_form",
        "synapse_browser_page_events", "synapse_browser_clock", "synapse_browser_aria_snapshot",
        "synapse_browser_scroll_into_view", "synapse_browser_set_content",
        "synapse_browser_wait_for_request", "synapse_browser_wait_for_response",
        "synapse_browser_wait_for_function", "synapse_browser_wait_for_load_state",
        "synapse_workspace_get", "synapse_workspace_put", "synapse_workspace_list",
        "synapse_workspace_subscribe", "synapse_timeline_get", "synapse_timeline_search",
        "synapse_timeline_stats", "synapse_timeline_digest", "synapse_timeline_pause",
        "synapse_timeline_resume", "synapse_timeline_purge", "synapse_timeline_redact",
        "synapse_episode_list", "synapse_episode_get", "synapse_episode_segment",
        "synapse_routine_list", "synapse_routine_inspect", "synapse_routine_mine",
        "synapse_routine_update", "synapse_routine_feedback", "synapse_routine_label_export",
        "synapse_intent_current", "synapse_intent_detect_tick", "synapse_suggestion_list",
        "synapse_suggestion_tick", "synapse_approval_list", "synapse_approval_decide",
        "synapse_approval_request", "synapse_agent_inbox", "synapse_agent_send",
        "synapse_agent_cost", "synapse_agent_stats", "synapse_agent_query",
        "synapse_agent_kill", "synapse_agent_pause", "synapse_agent_resume",
        "synapse_agent_steer", "synapse_session_list", "synapse_session_status",
        "synapse_session_end", "synapse_set_target", "synapse_clear_target",
        "synapse_health",
    ]
    idx.upsert_many(
        _entry(
            n,
            f"Synapse on-machine automation primitive for the desktop and browser ({n.split('_')[-1]}).",
            source="mcp:Synapse",
        )
        for n in synapse
    )
    return idx


# --------------------------------------------------------------------------- #
# The ceiling
# --------------------------------------------------------------------------- #


def test_advertised_payload_respects_gemini_ceiling(index):
    """The whole point: a 130-tool catalogue must not break the handshake."""
    stats = index.stats()
    assert stats.tools >= 120, "fixture must exceed the 128 ceiling to be meaningful"

    resolved = index.resolve("", budget=24)

    assert len(resolved["declarations"]) <= 24
    assert len(resolved["declarations"]) <= GEMINI_MAX_TOOLS
    assert resolved["deferred"] > 0, "tools should be deferred, not all advertised"


def test_budget_is_clamped_to_the_protocol_ceiling(index):
    """A caller asking for 5000 tools must not be handed 5000 tools."""
    resolved = index.resolve("control my computer", budget=5000)
    assert len(resolved["declarations"]) <= GEMINI_MAX_TOOLS


def test_declared_payload_fits_the_real_setup_frame(index):
    """Mimics what main.py actually sends: pinned + meta + retrieved."""
    from core.tool_index import META_DECLARATIONS

    resolved = index.resolve("search the web and open the browser", budget=24)
    total = len(resolved["declarations"]) + len(META_DECLARATIONS)
    assert total <= GEMINI_MAX_TOOLS


def test_pinned_tools_are_always_advertised(index):
    for query in ("", "play some music", "dim the monitors", "zzz qqq nonsense"):
        resolved = index.resolve(query, budget=8)
        names = {d["name"] for d in resolved["declarations"]}
        assert {"undo", "recall_memory"} <= names, f"pins lost for query {query!r}"


def test_meta_tools_are_available_for_discovery(index):
    from core.tool_index import META_DECLARATIONS

    assert {d["name"] for d in META_DECLARATIONS} == set(META_TOOLS)


# --------------------------------------------------------------------------- #
# Reachability: nothing becomes unreachable
# --------------------------------------------------------------------------- #


def test_every_tool_is_reachable_through_search(index):
    """A deferred tool must still be findable, or the index is lossy."""
    index.rebuild()
    for entry in index.entries():
        hits = index.search(entry.name, k=3)
        assert entry.name in [h.name for h in hits], f"{entry.name} unreachable by its own name"


def test_search_returns_relevant_tools(index):
    hits = index.search("adjust the monitor brightness", k=5)
    names = [h.name for h in hits]
    assert "system_diagnostics" in names

    hits = index.search("look up the latest news online", k=5)
    assert "web_search" in [h.name for h in hits]

    hits = index.search("search my memory vault", k=6)
    assert any(n.startswith("calyx_search") for n in [h.name for h in hits])


def test_paraphrase_reaches_the_right_tool(index):
    """BM25 alone would miss this; the n-gram scorer is what saves it."""
    hits = index.search("make the screen brighter", k=5)
    assert "system_diagnostics" in [h.name for h in hits]


def test_multi_step_request_covers_both_steps(index):
    """A compound ask must surface tools for both halves, not just the loud one."""
    hits = index.search("find the price of NVDA on the web and then launch the browser", k=10)
    names = [h.name for h in hits]
    assert "web_search" in names
    assert any("browser" in n for n in names), f"browser tools missing from {names}"


def test_empty_and_junk_queries_are_handled(index):
    assert index.search("") == []
    assert index.search("   ") == []
    assert isinstance(index.search("!!! ??? ..."), list)


def test_search_never_raises_on_fts_operator_soup(index):
    """User speech can contain FTS syntax; it must not blow up the query."""
    for nasty in ('AND OR NOT "', 'a*b', '(unbalanced', 'NEAR/10 "x', "-*"):
        assert isinstance(index.search(nasty, k=5), list)


# --------------------------------------------------------------------------- #
# Description collision / diversity
# --------------------------------------------------------------------------- #


def test_diversity_prevents_click_variants_from_flooding_results(index):
    """Many near-identical browser_* tools must not eat every slot."""
    hits = index.search("click something in the browser", k=5)
    names = [h.name for h in hits]
    assert len(set(names)) == len(names)
    # Synapse has 15+ *_browser_click / browser_* / cdp_* siblings; a working
    # ranker should still surface at least one non-Synapse capability.
    assert any(not n.startswith("synapse_") for n in names), f"no variety: {names}"


def test_mmr_keeps_relevance_first(index):
    hits = index.search("adjust the monitor brightness", k=4)
    assert hits[0].name == "system_diagnostics"


# --------------------------------------------------------------------------- #
# Backends / degradation
# --------------------------------------------------------------------------- #


def test_rebuild_activates_keyword_backends(index):
    stats = index.rebuild(force=True)
    assert "fts5" in stats.backends, "FTS5 must always be available (stdlib sqlite)"
    assert "ngram" in stats.backends, "n-gram scorer must always be available (numpy)"


def test_works_with_no_optional_dependency(tmp_path, monkeypatch):
    """With sentence-transformers absent the engine must still be functional."""
    monkeypatch.setattr(ti._EmbedBackend, "load", lambda self: False)
    idx = ToolIndex(cache_dir=tmp_path / "c")
    idx.upsert(_entry("system_diagnostics", "Display brightness and system health."))
    idx.upsert(_entry("web_search", "Search the web."))
    stats = idx.rebuild(force=True)

    assert "embed" not in stats.backends
    assert "fts5" in stats.backends
    assert idx.search("brightness", k=2)[0].name == "system_diagnostics"


def test_degrades_when_every_backend_fails(tmp_path, monkeypatch):
    """If retrieval is fully broken, the exact-name fallback must still work."""
    idx = ToolIndex(cache_dir=tmp_path / "c")
    idx.upsert(_entry("mcp_Calyx_kernel_answer", "Answer via the grounded kernel."))
    idx.rebuild(force=True)
    monkeypatch.setattr(idx._fts, "rank", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(idx._ngram, "rank", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(idx._embed, "rank", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    hits = idx.search("please run mcp_Calyx_kernel_answer", k=3)
    assert "mcp_Calyx_kernel_answer" in [h.name for h in hits]


def test_embed_backend_absent_does_not_raise(tmp_path):
    idx = ToolIndex(cache_dir=tmp_path / "c")
    idx.upsert(_entry("a_tool", "does a thing"))
    idx.rebuild(force=True)
    assert idx.search("thing", k=1)


def test_semantic_backend_improves_paraphrase_routing(tmp_path):
    """The optional semantic backend must earn its place.

    Keyword retrieval cannot connect "dim the monitors" to a tool described only
    as controlling brightness. This asserts the semantic backend exists, warms
    in the background without blocking, and then resolves the paraphrase.
    Skipped when no embedding runtime is installed — the engine is fully
    functional without it.
    """
    import time

    probe = _EmbedBackend()
    if not probe.load():
        pytest.skip("no embedding runtime installed (pip install fastembed)")

    idx = ToolIndex(cache_dir=tmp_path / "c")
    idx.upsert(_entry(
        "computer_settings",
        "Controls the computer: volume, brightness, window management, keyboard shortcuts.",
        keywords="dim screen display monitor",
    ))
    idx.upsert(_entry("web_search", "Searches the web for any information."))
    idx.upsert(_entry("spotify_controller", "Play, pause and control music playback."))

    started = time.perf_counter()
    idx.rebuild(force=True)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 250, f"rebuild must not block on the model ({elapsed_ms:.0f} ms)"

    deadline = time.time() + 90
    while time.time() < deadline and "embed" not in idx.stats().backends:
        time.sleep(0.5)
    assert "embed" in idx.stats().backends, "semantic backend never activated"

    started = time.perf_counter()
    hits = idx.search("dim the monitors", k=3)
    query_ms = (time.perf_counter() - started) * 1000
    assert "computer_settings" in [h.name for h in hits], [h.name for h in hits]
    assert query_ms < 250, f"semantic query too slow for the request path ({query_ms:.0f} ms)"


# --------------------------------------------------------------------------- #
# Catalog mutation
# --------------------------------------------------------------------------- #


def test_remove_source_drops_tools_and_manual(index):
    index.put_manual(SkillManual(skill_id="mcp:Synapse", body="Synapse rules."))
    index.rebuild(force=True)
    before = index.stats().tools

    removed = index.remove_source("mcp:Synapse")
    index.rebuild(force=True)

    assert removed > 0
    assert index.stats().tools == before - removed
    assert index.manual("mcp:Synapse") is None


def test_upsert_preserves_pin_on_replacement(tmp_path):
    idx = ToolIndex(cache_dir=tmp_path / "c")
    idx.upsert(_entry("undo", "original", pinned=True))
    idx.upsert(_entry("undo", "rewritten description"))
    assert idx.get("undo").pinned is True


def test_dynamic_mcp_connection_refreshes_index(tmp_path):
    """MCP tools land after startup; the index must pick them up on rebuild."""
    idx = ToolIndex(cache_dir=tmp_path / "c")
    idx.upsert(_entry("web_search", "Search the web."))
    idx.rebuild(force=True)
    assert "calyx_search" not in [h.name for h in idx.search("search the vault", k=3)]

    idx.upsert(_entry("calyx_search", "Search a Calyx vault.", source="mcp:Calyx"))
    idx.rebuild(force=True)

    assert "calyx_search" in [h.name for h in idx.search("search the vault", k=3)]


# --------------------------------------------------------------------------- #
# Skill manuals / JIT context
# --------------------------------------------------------------------------- #


def test_manual_is_injected_once_per_group_not_per_tool(index):
    index.put_manual(
        SkillManual(skill_id="mcp:Synapse", title="Synapse", body="Observe before acting. Ask first for spend.")
    )
    resolved = index.resolve("click in the browser", budget=24)
    ctx = resolved["skill_context"]

    assert "Observe before acting" in ctx
    # Many Synapse tools may be advertised, but the guide is injected once.
    assert ctx.count("Observe before acting") == 1
    assert ctx.startswith("[ACTIVE SKILL CONTEXT]")


def test_manual_is_absent_when_no_tool_from_that_group_is_advertised(index):
    index.put_manual(SkillManual(skill_id="mcp:NeverAdvertised", body="Secret manual."))
    resolved = index.resolve("adjust brightness", budget=4)
    assert "Secret manual" not in resolved["skill_context"]


def test_manual_lookup_accepts_bare_server_name(index):
    index.put_manual(SkillManual(skill_id="mcp:Calyx", body="Calyx grounding rules."))
    assert index.manual("Calyx") is not None
    assert index.manual("mcp:Calyx") is not None


def test_skill_context_respects_char_budget(index):
    index.put_manual(SkillManual(skill_id="mcp:Synapse", body="x" * 50_000))
    ctx = index.render_skill_context(["mcp:Synapse"], max_chars=1000)
    assert len(ctx) < 2000
    assert "truncated" in ctx


# --------------------------------------------------------------------------- #
# Cold start
# --------------------------------------------------------------------------- #


def test_cold_start_is_deterministic(index):
    """Same catalogue must produce the same handshake payload every time."""
    first = [d["name"] for d in index.resolve("", budget=10)["declarations"]]
    second = [d["name"] for d in index.resolve("", budget=10)["declarations"]]
    assert first == second


def test_cold_start_prefers_native_over_mcp(index):
    names = [d["name"] for d in index.resolve("", budget=8)["declarations"]]
    assert "web_search" in names


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def test_split_clauses_handles_compound_requests():
    assert ti._split_clauses("open the browser and then search the web") == [
        "open the browser", "search the web",
    ]
    assert ti._split_clauses("just one simple thing") == ["just one simple thing"]
    assert ti._split_clauses("") == []


def test_stopwords_do_not_dominate_scoring(index):
    hits = index.search("please can you do the thing for me", k=5)
    # A query of pure filler must not throw; ranking quality is not asserted
    # because there is no signal to rank on.
    assert isinstance(hits, list)


def test_stats_expose_payload_size(index):
    index.resolve("control the computer", budget=24)
    stats = index.stats()
    assert stats.advertised <= GEMINI_MAX_TOOLS
    assert stats.total_tokens_estimate > 0


# --------------------------------------------------------------------------- #
# Concurrency
# --------------------------------------------------------------------------- #


def test_repeated_rebuilds_do_not_deadlock(tmp_path, monkeypatch):
    """Regression: background semantic indexing must not lock-order-deadlock.

    ``rebuild`` runs under the index lock and schedules a worker that finishes
    by taking the index lock again. An earlier version had the worker hold the
    model lock while taking the index lock, which deadlocked the process on the
    *second* rebuild — a hang, not an exception, so it needs an explicit guard.

    The semantic backend is forced *on* here even though a model may not be
    installed, so the flag/lock dance is exercised without the wait.
    """
    import threading

    monkeypatch.setattr(ti._EmbedBackend, "load", lambda self: True)
    idx = ToolIndex(cache_dir=tmp_path / "c")
    for i in range(30):
        idx.upsert(_entry(f"tool_{i}", f"Does number {i}.", source="native"))

    done = threading.Event()

    def _cycle():
        for _ in range(5):
            idx.rebuild(force=True)
            idx.search("number three", k=5)
        done.set()

    worker = threading.Thread(target=_cycle, daemon=True)
    worker.start()
    assert done.wait(timeout=90), "rebuild cycle deadlocked"
    worker.join(timeout=5)
    assert not worker.is_alive()


def test_concurrent_search_is_safe(index, monkeypatch):
    """MCP registration and retrieval happen on different threads."""
    import threading

    # Force the semantic path too: that is where a mid-query matrix swap could
    # raise, since MCP servers re-index from their own loop.
    monkeypatch.setattr(ti._EmbedBackend, "load", lambda self: True)
    errors = []

    def _worker():
        try:
            for i in range(50):
                index.upsert(_entry(f"concurrent_{i}", f"Tool {i}.", source="mcp:Test"))
                index.search(f"tool {i}", k=3)
        except Exception as exc:  # pragma: no cover - reported via errors list
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, f"concurrent access raised: {errors[:3]}"