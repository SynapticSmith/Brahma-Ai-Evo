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
