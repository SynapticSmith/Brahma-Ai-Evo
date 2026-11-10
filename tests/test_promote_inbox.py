"""Unit tests for tools/promote_inbox.py (§7, §16 Decision 6)."""
from __future__ import annotations

from pathlib import Path
from tools.promote_inbox import list_inbox_notes, promote_note


def test_promote_note_lifecycle(tmp_path: Path):
    vault = tmp_path / "vault"
    inbox_dir = vault / "wiki" / "library" / "knowledge" / "_inbox" / "unsorted" / "test-book"
    inbox_dir.mkdir(parents=True)

    note_file = inbox_dir / "test-note.md"
    note_content = """---
type: ConceptNote
title: "Test Concept"
domain: humanities/philosophy
status: draft
confidence: 0.35
---
# Test Concept Body
This is a test concept from an inboxed companion note.
"""
    note_file.write_text(note_content, encoding="utf-8")

    # 1. Test listing
    notes = list_inbox_notes(vault=vault)
    assert len(notes) == 1
    assert notes[0]["name"] == "test-note.md"
    assert notes[0]["status"] == "draft"

    # 2. Test dry-run promotion
    ok, msg, target = promote_note(
        note_file,
        "humanities/philosophy/eastern-philosophy/buddhism",
        vault=vault,
        dry_run=True,
    )
    assert ok
    assert "Would move" in msg
    assert note_file.is_file()  # Not moved in dry-run

    # 3. Test invalid domain rejection
    bad_ok, bad_msg, _ = promote_note(
        note_file,
        "completely/invalid/nonexistent/domain",
        vault=vault,
        dry_run=False,
    )
    assert not bad_ok
    assert "Invalid target taxonomy domain" in bad_msg

    # 4. Test real promotion
    real_ok, real_msg, real_target = promote_note(
        note_file,
        "humanities/philosophy/eastern-philosophy/buddhism",
        vault=vault,
        dry_run=False,
    )
    assert real_ok
    assert real_target is not None
    assert real_target.is_file()
    assert not note_file.exists()  # Removed from inbox

    # Check updated frontmatter
    promoted_text = real_target.read_text(encoding="utf-8")
    assert "domain: humanities/philosophy/eastern-philosophy/buddhism" in promoted_text
    assert "status: reviewed" in promoted_text
    assert "promoted_at:" in promoted_text
