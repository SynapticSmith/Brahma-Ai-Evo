"""Unit tests for the knowledge extraction and placement pipeline (§7)."""
from __future__ import annotations

from pathlib import Path

from tools import extract_knowledge


def test_segmentation_preserves_headings_and_bounds():
    sample = (
        "# Main Title\n\n"
        "## Chapter 1: Ontological Principles\n\n"
        + ("This is a substantive sentence on ontological necessity and metaphysics. " * 15)
        + "\n\n"
        + "## Chapter 2: The Privative Nature of Avijja\n\n"
        + ("Agnosis or avijja is privation of illumination from itself. " * 15)
    )

    sections = extract_knowledge.segment_markdown(sample)
    assert len(sections) == 2
    assert "Chapter 1" in sections[0]["heading"]
    assert "Chapter 2" in sections[1]["heading"]
    assert all(len(s["body"]) >= extract_knowledge.MIN_UNIT_CHARS for s in sections)


def test_classification_and_placement_in_spine(tmp_path: Path):
    vault = tmp_path / "vault"
    fulltext_dir = vault / "book-library" / "fulltext"
    knowledge_dir = vault / "wiki" / "library" / "knowledge"
    fulltext_dir.mkdir(parents=True)
    knowledge_dir.mkdir(parents=True)

    companion_file = fulltext_dir / "wheeler-avijja.md"
    companion_file.write_text(
        "---\n"
        "title: Avijja and Emanationism\n"
        "slug: wheeler-avijja\n"
        "author: Ken Lee Wheeler\n"
        "tags: [humanities.philosophy.buddhism]\n"
        "---\n\n"
        "# Avijja and Emanationism\n\n"
        "## The Metaphysics of Avijja in Early Buddhism\n\n"
        + ("In early Buddhism and the Nikayas, avijja is the beginningless privation of gnosis. "
           "The nature of Atman and Brahman in the Upanishads corresponds directly to vijja. " * 12)
        + "\n\n"
        "## Unrelated Cooking Recipe\n\n"
        + ("To bake sourdough bread you need warm water, active flour culture, and fine sea salt. " * 15)
        + "\n",
        encoding="utf-8",
    )

    result = extract_knowledge.extract_units_from_file(
        companion_file,
        vault=vault,
        confidence_threshold=0.60,
        draft_threshold=0.45,
    )

    assert result["ok"] is True
    assert result["sections_total"] == 2
    assert result["placed"] >= 1

    # First section should be placed in Buddhism or Philosophy spine
    placed = result["placed_units"]
    assert any("buddhism" in u["domain"] or "philosophy" in u["domain"] for u in placed)

    # Verify that files were written to disk
    placed_slug = placed[0]["slug"]
    found_notes = list(knowledge_dir.rglob(f"{placed_slug}.md"))
    assert len(found_notes) == 1

    note_text = found_notes[0].read_text(encoding="utf-8")
    assert "type: ConceptNote" in note_text
    assert "wheeler-avijja" in note_text


def test_low_confidence_routes_to_inbox(tmp_path: Path):
    vault = tmp_path / "vault"
    fulltext_dir = vault / "book-library" / "fulltext"
    knowledge_dir = vault / "wiki" / "library" / "knowledge"
    fulltext_dir.mkdir(parents=True)
    knowledge_dir.mkdir(parents=True)

    companion_file = fulltext_dir / "random-notes.md"
    companion_file.write_text(
        "---\ntitle: Random Notes\nslug: random-notes\n---\n\n"
        "# Random Notes\n\n"
        "## Arbitrary Section\n\n"
        + ("Testing obscure random phrases xylophone zebra quantum quasar widget gadget. " * 15),
        encoding="utf-8",
    )

    result = extract_knowledge.extract_units_from_file(
        companion_file,
        vault=vault,
        confidence_threshold=0.85,
        draft_threshold=0.75,  # High threshold forces inboxing
    )

    assert result["inboxed"] == 1
    inbox_files = list((knowledge_dir / "_inbox" / "unsorted" / "random-notes").glob("*.md"))
    assert len(inbox_files) == 1
    assert "ConceptNote" in inbox_files[0].read_text(encoding="utf-8")
