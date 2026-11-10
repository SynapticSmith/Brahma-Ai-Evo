"""Tests for Brahma AI Vault Bootstrap Utility (§5, §8, §12)."""
from __future__ import annotations

from pathlib import Path

from tools.bootstrap_vault import (
    bootstrap_vault,
    scaffold_vault_structure,
    seed_canonical_nodes,
)


def test_scaffold_vault_structure(tmp_path: Path):
    target = tmp_path / "TestVault"
    paths = scaffold_vault_structure(target)

    assert paths["vault_dir"].is_dir()
    assert (target / "wiki" / "library" / "knowledge" / "_templates" / "ConceptNote.md").is_file()
    assert (target / "wiki" / "library" / "knowledge" / "_templates" / "BookNote.md").is_file()
    assert (target / "wiki" / "library" / "knowledge" / "_templates" / "SynthesisNote.md").is_file()
    assert (target / "wiki" / "library" / "methodology" / "OKF-Protocol.md").is_file()
    assert (target / "wiki" / "library" / "methodology" / "ICM-Protocol.md").is_file()
    assert (target / "wiki" / "library" / "knowledge" / "_taxonomy" / "taxonomy.json").is_file()
    assert (target / "README.md").is_file()


def test_seed_canonical_nodes_creates_122_nodes(tmp_path: Path):
    target = tmp_path / "TestVault"
    scaffold_vault_structure(target)
    count = seed_canonical_nodes(target)

    assert count == 122
    # Check that a few specific major nodes exist
    knowledge_dir = target / "wiki" / "library" / "knowledge"
    assert (knowledge_dir / "humanities" / "index.md").is_file()
    assert (knowledge_dir / "humanities" / "wikipedia.md").is_file()
    assert (knowledge_dir / "formal-sciences" / "mathematics" / "index.md").is_file()
    assert (knowledge_dir / "formal-sciences" / "computer-science" / "index.md").is_file()
    assert (knowledge_dir / "applied-sciences" / "engineering-and-technology" / "software-engineering" / "index.md").is_file()

    # Verify frontmatter on a seeded node
    index_text = (knowledge_dir / "humanities" / "index.md").read_text(encoding="utf-8")
    assert "type: ConceptIndex" in index_text
    assert 'title: "Humanities"' in index_text
    assert "license: CC-BY-SA-4.0" in index_text


def test_bootstrap_vault_end_to_end_no_index(tmp_path: Path):
    target = tmp_path / "TestVaultFull"
    res = bootstrap_vault(
        vault_dir=target,
        rebuild_index=False,
        configure_app=False,
    )

    assert res["ok"] is True
    assert res["nodes_seeded"] == 122
    assert (target / "wiki" / "library" / "knowledge" / "natural-sciences" / "physics" / "index.md").is_file()
