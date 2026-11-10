"""Second-brain index, retrieval injection, and the brain_* tools."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import second_brain
from agent.context import ConversationContext
from agent.loop import AgentLoop
from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    (root / "wiki" / "entities").mkdir(parents=True)
    (root / "raw").mkdir()
    (root / "ai-chats").mkdir()
    (root / "subagent-orchestration" / "agents").mkdir(parents=True)

    (root / "wiki" / "entities" / "calyx.md").write_text(
        "---\ntitle: Calyx\ntype: entity\n---\n"
        "# Calyx\n\n"
        "Calyx stores each input as a constellation measured through independent "
        "lenses and keeps a hash-chained ledger of provenance.\n\n"
        "## Lenses\n\nA vector database is a one-lens Calyx.\n",
        encoding="utf-8",
    )
    (root / "raw" / "source.md").write_text(
        "# Source\n\nThe provenance ledger is append-only and tamper evident.\n",
        encoding="utf-8",
    )
    (root / "ai-chats" / "secret.md").write_text(
        "# Secret\n\nThis excluded chat mentions calyx provenance.\n",
        encoding="utf-8",
    )
    (root / "subagent-orchestration" / "agents" / "tpl.md").write_text(
        "# Template\n\nExcluded agent template about calyx.\n",
        encoding="utf-8",
    )

    data_dir = tmp_path / "brahma-data"
    monkeypatch.setattr(second_brain, "_data_dir", lambda: data_dir)
    monkeypatch.setenv("BRAHMA_SECOND_BRAIN_VAULT", str(root))
    monkeypatch.setenv("BRAHMA_EMBED_ENDPOINT", "http://127.0.0.1:9/v1")
    second_brain.clear_cache()
    return root


def test_chunking_packs_sections_instead_of_one_per_heading():
    body = "\n".join(
        f"## Section {i}\n\n" + ("some durable knowledge sentence. " * 12)
        for i in range(20)
    )
    chunks = second_brain.chunk_text(body)
    assert 1 <= len(chunks) <= 5
    assert all(len(text) <= second_brain.CHUNK_CHARS + 200 for _, text in chunks)
    # The heading line survives inside the chunk so structure is not lost.
    assert "## Section 0" in chunks[0][1]


def test_sync_indexes_pages_and_skips_excluded(vault: Path):
    stats = second_brain.sync(embed=False)
    assert stats["ok"] is True
    assert stats["pages_indexed"] == 2  # calyx.md and raw/source.md

    info = second_brain.status()
    assert info["pages"] == 2
    assert info["chunks"] >= 2
    assert info["index_exists"] is True


def test_search_finds_keyword_without_embeddings(vault: Path):
    second_brain.sync(embed=False)
    results = second_brain.search("hash-chained provenance ledger", k=5)
    assert results
    paths = {item["path"] for item in results}
    assert "wiki/entities/calyx.md" in paths
    # The excluded shelves must never surface.
    assert not any("ai-chats" in path for path in paths)
    assert not any("subagent-orchestration/agents" in path for path in paths)


def test_context_for_turn_appends_calyx_provenance(vault: Path, monkeypatch):
    from agent import calyx_bridge

    second_brain.sync(embed=False)
    monkeypatch.setattr(
        calyx_bridge,
        "fast_search",
        lambda query, k=3, vault="obsidian-wiki": [
            {
                "cx_id": "abcdef1234567890",
                "provenance": {
                    "ledger_seq": 7,
                    "chain_hash": "deadbeefcafebabe00",
                },
            }
        ],
    )

    block = second_brain.context_for_turn("How does Calyx keep provenance?")

    assert "CALYX GROUNDED PROVENANCE" in block
    assert "ledger_seq=7" in block
    assert "chain_hash=deadbeefcafebabe" in block


def test_context_for_turn_renders_a_bounded_block(vault: Path):
    second_brain.sync(embed=False)
    block = second_brain.context_for_turn("How does Calyx keep provenance?")
    assert block.startswith("SECOND BRAIN")
    assert "wiki/entities/calyx.md" in block
    assert len(block) <= second_brain.config()["context_chars"] + 400


def test_should_retrieve_gate():
    assert second_brain.should_retrieve("How does Calyx store constellations?")
    assert not second_brain.should_retrieve("hi")
    assert not second_brain.should_retrieve("/resume")
    assert not second_brain.should_retrieve("git status")
    assert not second_brain.should_retrieve("pip install requests")
    # A single content word is still a knowledge question.
    assert second_brain.should_retrieve("calyx")


def test_short_queries_must_match_every_term(vault: Path):
    second_brain.sync(embed=False)
    assert second_brain.context_for_turn("calyx provenance") != ""
    # "weather in Paris" must not be answered with notes that only match
    # "weather"; "capital of France" must not match "social capital".
    assert second_brain.context_for_turn("weather in Paris") == ""
    assert second_brain.context_for_turn("capital of France") == ""


def test_vector_fusion_waits_for_coverage(vault: Path, monkeypatch):
    second_brain.sync(embed=False)
    calls: list[str] = []
    monkeypatch.setattr(
        second_brain,
        "_embed_query",
        lambda text: calls.append(text) or [0.0] * second_brain.EMBED_DIM,
    )
    second_brain.search("provenance ledger")
    assert calls == []  # 0/2 chunks embedded -> keyword only

    monkeypatch.setattr(second_brain, "_vector_coverage", lambda conn: 1.0)
    second_brain.search("provenance ledger")
    assert calls == ["provenance ledger"]


def test_status_reports_vector_coverage(vault: Path):
    second_brain.sync(embed=False)
    info = second_brain.status()
    assert info["vectors_missing"] == info["chunks"]
    assert info["vector_coverage"] == 0.0
    assert info["semantic_enabled"] is False


def test_read_page_cannot_escape_the_vault(vault: Path):
    text = second_brain.read_page("wiki/entities/calyx.md")
    assert "hash-chained ledger" in text

    with pytest.raises(ValueError):
        second_brain.read_page("../../outside.md")
    with pytest.raises(ValueError):
        second_brain.read_page("C:/Windows/win.ini")


def test_read_page_rejects_non_markdown(vault: Path):
    (vault / "notes.txt").write_text("not markdown", encoding="utf-8")
    with pytest.raises(ValueError):
        second_brain.read_page("notes.txt")


def test_retrieval_is_injected_before_the_last_user_message():
    context = ConversationContext(system="SYSTEM")
    context.append_user("first question")
    context.append({"role": "assistant", "content": "first answer"})
    context.append_user("second question")
    context.set_retrieval("SECOND BRAIN BLOCK")

    payload = context.as_payload()
    roles = [message["role"] for message in payload]
    assert roles == ["system", "user", "assistant", "system", "user"]
    assert payload[-2]["content"] == "SECOND BRAIN BLOCK"
    assert payload[-1]["content"] == "second question"
    # The injected block is request context, not transcript.
    assert all(m.get("content") != "SECOND BRAIN BLOCK" for m in context.messages)


def test_loop_injects_retrieval_without_persisting_it(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        second_brain, "context_for_turn", lambda text, budget=None: "SECOND BRAIN BLOCK"
    )
    calls: list[list[dict]] = []

    def complete(messages, tools):
        calls.append([dict(m) for m in messages])
        return {"role": "assistant", "content": "answered"}

    runtime = AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict(
            {"classes": {"read": "allow", "write": "allow", "native": "allow"}}
        ),
        headless=True,
    )
    result = AgentLoop(complete, runtime=runtime, max_turns=2).run("what is calyx?")

    assert result.text == "answered"
    assert calls
    first = calls[0]
    assert first[-2]["role"] == "system"
    assert first[-2]["content"] == "SECOND BRAIN BLOCK"
    assert first[-1]["content"] == "what is calyx?"
    assert all(m.get("content") != "SECOND BRAIN BLOCK" for m in result.messages)


def test_brain_tools_are_registered():
    from agent import brain_tools
    from agent import spec

    for name in brain_tools.BRAIN_TOOL_NAMES:
        assert spec.get(name) is not None


def test_brain_search_tool_returns_paths(vault: Path):
    from agent import brain_tools
    from agent.spec import ToolContext

    second_brain.sync(embed=False)
    text = brain_tools.brain_search(
        {"query": "provenance ledger"}, ToolContext(workspace=vault)
    )
    assert "wiki/entities/calyx.md" in text or "raw/source.md" in text


def test_brain_status_tool_is_json(vault: Path):
    from agent import brain_tools
    from agent.spec import ToolContext

    second_brain.sync(embed=False)
    data = json.loads(brain_tools.brain_status({}, ToolContext(workspace=vault)))
    assert data["pages"] == 2
    assert data["index_exists"] is True


def test_brain_sync_tool_caps_embeddings(vault: Path, monkeypatch):
    from agent import brain_tools
    from agent.spec import ToolContext

    captured: dict = {}

    def fake_sync(**kwargs):
        captured.clear()
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(second_brain, "sync", fake_sync)
    context = ToolContext(workspace=vault)

    brain_tools.brain_sync({}, context)
    assert captured["embed_limit"] == brain_tools.TOOL_EMBED_LIMIT

    brain_tools.brain_sync({"embed_limit": 10_000}, context)
    assert captured["embed_limit"] == brain_tools.TOOL_EMBED_LIMIT_MAX

    brain_tools.brain_sync({"embed": False}, context)
    assert captured["embed_limit"] is None


def test_sync_shelf_backfill_stays_inside_the_requested_shelf(
    vault: Path, monkeypatch
):
    (vault / "wiki" / "concepts").mkdir(parents=True)
    (vault / "wiki" / "concepts" / "other.md").write_text(
        "# Other\n\nA note about the other shelf and its own knowledge.\n",
        encoding="utf-8",
    )
    second_brain.sync(embed=False)

    embedded: list[str] = []

    def fake_embed(texts, *, prefixes=True):
        embedded.extend(texts)
        return [[0.01] * second_brain.EMBED_DIM for _ in texts]

    monkeypatch.setattr(second_brain, "_embed_documents", fake_embed)
    stats = second_brain.sync(shelf="wiki/entities", embed=True, embed_limit=8)

    assert stats["vectors"] == 1
    assert any("Calyx" in text for text in embedded)
    assert not any("other shelf" in text for text in embedded)


def test_with_retrieval_does_not_mutate_the_seed(monkeypatch):
    monkeypatch.setattr(
        second_brain,
        "context_for_turn",
        lambda text, budget=None: "SECOND BRAIN BLOCK",
    )
    messages = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second"},
    ]
    preview, block = second_brain.with_retrieval(messages, "second")

    assert block == "SECOND BRAIN BLOCK"
    assert preview[-2]["content"] == "SECOND BRAIN BLOCK"
    assert preview[-1]["content"] == "second"
    assert len(preview) == len(messages) + 1
    assert all(m.get("content") != "SECOND BRAIN BLOCK" for m in messages)


def test_loop_reuses_a_precomputed_retrieval_block(tmp_path: Path, monkeypatch):
    queries: list[str] = []

    def fake_context(text, budget=None):
        queries.append(text)
        return "FRESH BLOCK"

    monkeypatch.setattr(second_brain, "context_for_turn", fake_context)
    calls: list[list[dict]] = []

    def complete(messages, tools):
        calls.append([dict(m) for m in messages])
        return {"role": "assistant", "content": "answered"}

    runtime = AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict(
            {"classes": {"read": "allow", "write": "allow", "native": "allow"}}
        ),
        headless=True,
    )
    result = AgentLoop(
        complete, runtime=runtime, max_turns=1, retrieval="PRECOMPUTED BLOCK"
    ).run("what is calyx?")

    assert result.text == "answered"
    assert queries == []  # the host already rendered this turn's block
    assert calls[0][-2] == {"role": "system", "content": "PRECOMPUTED BLOCK"}
    assert calls[0][-1]["content"] == "what is calyx?"


def test_voice_brain_map_does_not_promise_automatic_retrieval(vault: Path):
    second_brain.sync(embed=False)
    voice = second_brain.brain_map(automatic=False)
    assert "brain_search" in voice
    assert "retrieved automatically" not in voice
    assert "retrieved automatically" in second_brain.brain_map()


def test_schema_v2_migration_from_v1(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Create legacy v1 schema
    conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta(key, value) VALUES ('schema', '1');
        CREATE TABLE pages (
            path  TEXT PRIMARY KEY,
            mtime REAL NOT NULL,
            size  INTEGER NOT NULL,
            sha   TEXT NOT NULL,
            title TEXT,
            shelf TEXT
        );
        CREATE TABLE chunks (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            path    TEXT NOT NULL,
            ord     INTEGER NOT NULL,
            heading TEXT,
            text    TEXT NOT NULL
        );
        INSERT INTO pages VALUES ('wiki/library/knowledge/humanities/philosophy/index.md', 1.0, 10, 'sha1', 'Philosophy', 'wiki');
        INSERT INTO pages VALUES ('book-library/fulltext/sample.md', 1.0, 20, 'sha2', 'Sample Book', 'book-library');
        INSERT INTO chunks VALUES (1, 'wiki/library/knowledge/humanities/philosophy/index.md', 0, 'Overview', 'Philosophy overview');
        INSERT INTO chunks VALUES (2, 'book-library/fulltext/sample.md', 0, 'Chapter 1', 'Book content');
        """
    )
    conn.commit()

    # Now run _ensure_schema
    second_brain._ensure_schema(conn)

    schema_version = int(second_brain._get_meta(conn, "schema"))
    assert schema_version == 2

    pages_cols = {row["name"] for row in conn.execute("PRAGMA table_info(pages)")}
    assert {"kind", "domain", "quality"}.issubset(pages_cols)

    chunks_cols = {row["name"] for row in conn.execute("PRAGMA table_info(chunks)")}
    assert "kind" in chunks_cols

    # Check backfilled values
    p1 = conn.execute("SELECT * FROM pages WHERE path LIKE '%index.md'").fetchone()
    assert p1["kind"] == "index"
    assert p1["domain"] == "humanities/philosophy"

    p2 = conn.execute("SELECT * FROM pages WHERE path LIKE 'book-library%'").fetchone()
    assert p2["kind"] == "book"

    c1 = conn.execute("SELECT * FROM chunks WHERE path LIKE '%index.md'").fetchone()
    assert c1["kind"] == "index"

    c2 = conn.execute("SELECT * FROM chunks WHERE path LIKE 'book-library%'").fetchone()
    assert c2["kind"] == "book"
    conn.close()


def test_classify_kind_and_domain():
    assert second_brain._classify_kind("wiki/library/knowledge/humanities/philosophy/index.md") == "index"
    assert second_brain._classify_kind("wiki/library/knowledge/humanities/philosophy/wikipedia.md") == "reference"
    assert second_brain._classify_kind("book-library/fulltext/book1.md") == "book"
    assert second_brain._classify_kind("wiki/synthesis/overview.md") == "synthesis"
    assert second_brain._classify_kind("wiki/entities/calyx.md") == "entity"
    assert second_brain._classify_kind("wiki/library/knowledge/humanities/philosophy/note.md") == "concept"
    # Frontmatter override
    assert second_brain._classify_kind("custom.md", {"type": "BookNote"}) == "book"
    assert second_brain._classify_kind("custom.md", {"type": "SynthesisNote"}) == "synthesis"

    assert (
        second_brain._extract_domain("wiki/library/knowledge/humanities/philosophy/epistemology/note.md")
        == "humanities/philosophy/epistemology"
    )
    assert second_brain._extract_domain("other.md", {"domain": "custom/domain"}) == "custom/domain"

    assert second_brain._extract_quality({"status": "reviewed"}) == 1.0
    assert second_brain._extract_quality({"status": "draft"}) == 0.5
    assert second_brain._extract_quality({"status": "deprecated"}) == 0.2


def test_type_weighted_ranking_and_max_two_index_pages(vault: Path):
    # Add multiple index pages and concept notes
    for i in range(4):
        p = vault / "wiki" / f"topic_{i}"
        p.mkdir(parents=True, exist_ok=True)
        (p / "index.md").write_text(
            f"---\ntitle: Index {i}\n---\n# Index {i}\nInformation retrieval epistemology concept note {i}\n",
            encoding="utf-8",
        )
        (p / "concept.md").write_text(
            f"---\ntitle: Concept {i}\n---\n# Concept {i}\nInformation retrieval epistemology concept note {i}\n",
            encoding="utf-8",
        )

    second_brain.sync(embed=False, rebuild=True)
    results = second_brain.search("information retrieval epistemology", k=8)
    assert len(results) > 2

    # Check max 2 index pages rule
    index_results = [r for r in results if r.get("kind") == "index" or r["path"].endswith("index.md")]
    assert len(index_results) <= 2

    # Verify concept notes rank ahead of index pages with identical BM25 text
    first_kind = results[0].get("kind")
    assert first_kind in ("concept", "entity", "synthesis")


def test_status_reports_breakdowns_by_kind(vault: Path):
    second_brain.sync(embed=False)
    info = second_brain.status()
    assert info["schema_version"] == 2
    assert "pages_by_kind" in info
    assert "chunks_by_kind" in info
    assert "vectors_missing_by_kind" in info
    assert info["pages_by_kind"].get("entity", 0) >= 1


def test_taxonomy_navigation_browse_and_locate(vault: Path):
    # Browse root returns canonical divisions
    root_browse = second_brain.browse_node("")
    assert root_browse["division"] == "root"
    assert len(root_browse["children"]) == 5
    assert any(c["slug"] == "humanities" for c in root_browse["children"])

    # Browse valid spine node
    phil_browse = second_brain.browse_node("humanities/philosophy")
    if "error" not in phil_browse:
        assert phil_browse["division"] == "humanities"
        assert phil_browse["title"] == "Philosophy"
        assert len(phil_browse["children"]) >= 1

    # Browse unknown path returns structured error
    err_browse = second_brain.browse_node("nonexistent/discipline")
    assert err_browse["error"] == "taxonomy/unknown_path"
    assert "valid_divisions" in err_browse

    # Locate nodes
    loc = second_brain.locate_nodes("philosophy", limit=5)
    assert loc
    assert any("philosophy" in c["path"] for c in loc)
    assert loc[0]["confidence"] >= 0.8


def test_brain_tools_browse_and_locate(vault: Path):
    from agent import brain_tools
    from agent.spec import ToolContext

    ctx = ToolContext(workspace=vault)
    browse_json = brain_tools.brain_browse({"path": ""}, ctx)
    browse_data = json.loads(browse_json)
    assert browse_data["division"] == "root"

    locate_json = brain_tools.brain_locate({"query": "philosophy"}, ctx)
    locate_data = json.loads(locate_json)
    assert locate_data["count"] >= 1
    assert "candidates" in locate_data


def test_retrieval_metadata_and_telemetry(vault: Path):
    second_brain.sync(embed=False)
    block = second_brain.context_for_turn("calyx provenance")
    meta = second_brain.retrieval_metadata(block)
    assert "sources" in meta
    assert len(meta["sources"]) >= 1
    assert "kinds" in meta
    assert meta["kinds"].get("entity", 0) >= 1
