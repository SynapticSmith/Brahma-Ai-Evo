"""Unit tests for Calyx bridge and Second Brain starter template."""
from __future__ import annotations

from pathlib import Path

from agent import calyx_bridge
from tools import init_second_brain


def test_extract_ingest_text_parses_frontmatter_and_body():
    raw = (
        "---\n"
        "type: Concept\n"
        'title: "Anatta Concept"\n'
        'description: "A reading of anatta."\n'
        "tags: [philosophy, buddhism]\n"
        "---\n"
        "# Overview\n\n"
        "This is the body of the note."
    )
    title, desc, body = calyx_bridge.extract_ingest_text(raw)
    assert title == "Anatta Concept"
    assert desc == "A reading of anatta."
    assert "This is the body of the note." in body


def test_extract_ingest_text_falls_back_to_markdown_heading():
    raw = "# Direct Heading\n\nSome body text."
    title, desc, body = calyx_bridge.extract_ingest_text(raw)
    assert title == "Direct Heading"
    assert desc == ""
    assert "Some body text." in body


def test_calyx_search_guards_empty_query():
    assert calyx_bridge.search("") == []
    assert calyx_bridge.search("   ") == []


def test_calyx_kernel_answer_guards_empty_query():
    assert calyx_bridge.kernel_answer("") == {}


def test_anchor_label_keeps_short_basenames_and_disambiguates_long_ones():
    assert calyx_bridge.anchor_label("wiki/entities/calyx.md") == "calyx"

    long_one = "wiki/concepts/" + "a" * 40 + "-one.md"
    long_two = "wiki/concepts/" + "a" * 40 + "-two.md"
    label_one = calyx_bridge.anchor_label(long_one)
    label_two = calyx_bridge.anchor_label(long_two)

    assert label_one != label_two
    assert len(label_one) <= 32
    assert len(label_two) <= 32


def test_fast_search_parks_after_a_failure(monkeypatch):
    monkeypatch.setattr(calyx_bridge, "is_available", lambda: True)
    monkeypatch.setattr(calyx_bridge, "_fast_blocked_until", 0.0)
    calls: list[float | None] = []

    def fail(query, k, vault, timeout):
        calls.append(timeout)
        raise RuntimeError("calyx is hung")

    monkeypatch.setattr(calyx_bridge, "_search", fail)

    assert calyx_bridge.fast_search("what is calyx") == []
    assert calls == [calyx_bridge.FAST_TIMEOUT]
    # The next turn skips Calyx instead of paying the timeout again.
    assert calyx_bridge.fast_search("what is calyx") == []
    assert calls == [calyx_bridge.FAST_TIMEOUT]


def test_fast_search_does_not_park_on_a_successful_empty_result(monkeypatch):
    monkeypatch.setattr(calyx_bridge, "is_available", lambda: True)
    monkeypatch.setattr(calyx_bridge, "_fast_blocked_until", 0.0)
    monkeypatch.setattr(
        calyx_bridge, "_search", lambda query, k, vault, timeout: []
    )

    assert calyx_bridge.fast_search("anything at all") == []
    assert calyx_bridge._fast_blocked_until == 0.0


def test_template_copy_creates_canonical_structure(tmp_path: Path):
    dest = tmp_path / "new_vault"
    copied = init_second_brain.copy_template(dest)
    assert copied >= 8
    assert (dest / "README.md").is_file()
    assert (dest / "CONTEXT.md").is_file()
    assert (dest / "AGENTS.md").is_file()
    assert (dest / "wiki" / "CLAUDE.md").is_file()
    assert (dest / "wiki" / "hot.md").is_file()
    assert (dest / "wiki" / "entities" / "welcome.md").is_file()

    readme_text = (dest / "README.md").read_text(encoding="utf-8")
    assert "ICM" in readme_text or "Interpretable Context" in readme_text
    assert "OKF" in readme_text or "Open Knowledge" in readme_text


def test_ingest_book_helpers(tmp_path: Path):
    from tools import ingest_book

    sample = tmp_path / "sample.txt"
    sample.write_text("Chapter 1: The Core Doctrine\n\nSome text on philosophy.", encoding="utf-8")

    meta, body = ingest_book.extract_plain_text(sample)
    assert meta["title"] == "sample"
    assert "Chapter 1" in body

    note = ingest_book.build_okf_note(
        title="Sample Book",
        authors=["Author Name"],
        tags=["philosophy"],
        source_path=str(sample),
        body=body,
        domain="humanities/philosophy",
    )
    assert "type: BookNote" in note
    assert "title: \"Sample Book\"" in note
    assert "Author Name" in note
    assert "humanities" in note
