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
