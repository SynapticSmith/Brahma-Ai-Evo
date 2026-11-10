"""Brahma AI — Vault Bootstrap & Seeding Utility (§5, §8, §12).

Scaffolds a complete, self-contained Obsidian Second Brain for new users:
1. Creates canonical 5-division directory structure (Humanities, Social Sciences,
   Natural Sciences, Formal Sciences, Applied Sciences) under `wiki/library/knowledge/`.
2. Scaffolds ICM & OKF directories: `_inbox/`, `_templates/`, `methodology/`, `references/`.
3. Installs canonical OKF v0.2 note templates (`ConceptNote`, `BookNote`, `SynthesisNote`).
4. Seeds all 122 academic taxonomy spine nodes with canonical `index.md` and companion
   reference notes from `config/taxonomy.json` and local cache.
5. Updates Brahma AI's local configuration (`%LOCALAPPDATA%\\BrahmaAI\\second_brain\\config.json`).
6. Rebuilds the second brain search index (`index.db`) for immediate out-of-the-box search.

Usage:
    python tools/bootstrap_vault.py
    python tools/bootstrap_vault.py --vault-dir "C:\\Users\\Admin\\Documents\\Obsidian\\Brahma Vault"
    python tools/bootstrap_vault.py --no-index
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent import second_brain
from tools.seed_academic_taxonomy import (
    get_cache_dir,
    seed_node,
)
from tools.taxonomy import load_taxonomy

TEMPLATE_CONCEPT_NOTE = """---
type: ConceptNote
id: "concept-slug"
title: "Concept Title"
domain: "humanities/philosophy"
level: 1
status: draft
tags: [concept]
aliases: []
sources: []
created: "YYYY-MM-DD"
---

# Concept Title

## Definition & Core Thesis
<!-- Level 0 Summary: 1-2 sentence core thesis / formal definition -->

## Context & Key Insights
<!-- Level 1 Summary: Bulleted insights, key arguments, and principles -->

## Detailed Analysis & Evidence
<!-- Level 2 Summary: Deep explanation, mechanisms, proofs, and historical context -->

## Cross-Disciplinary Connections
<!-- Wikilinks to related concept notes, parent discipline index, or source texts -->
- [[index|Parent Discipline Index]]
"""

TEMPLATE_BOOK_NOTE = """---
type: BookNote
id: "book-slug"
title: "Book Title"
author: "Author Name"
domain: "applied_sciences/computing"
level: 1
status: read
tags: [book]
isbn: ""
published_year: 2026
created: "YYYY-MM-DD"
---

# Book Title by Author Name

## Executive Summary
<!-- Level 0 Summary: High-level overview of the entire book's thesis -->

## Key Takeaways
<!-- Level 1 Summary: Top actionable or conceptual insights -->

## Chapter Syntheses
<!-- Level 2 Summaries: Section-by-section breakdown of core arguments -->

## Extracted Concepts
<!-- List of atomic ConceptNotes extracted from this book -->
"""

TEMPLATE_SYNTHESIS_NOTE = """---
type: SynthesisNote
id: "synthesis-slug"
title: "Synthesis Title"
domains: ["formal_sciences/mathematics", "natural_sciences/physics"]
level: 2
status: active
tags: [synthesis]
created: "YYYY-MM-DD"
---

# Synthesis Title

## Problem Statement & Cross-Cutting Scope
<!-- Why these distinct fields or perspectives need unification -->

## Comparative Analysis & Synergies
<!-- Structured comparison of models, terminology, and theoretical tensions -->

## Unified Architectural Model / Framework
<!-- The synthesized thesis and operational model -->

## Referenced Concepts & Sources
"""

METHODOLOGY_OKF = """# Open Knowledge Framework (OKF) Protocol v0.2

The Open Knowledge Framework organizes knowledge into atomic, progressive, and machine-navigable notes.

## 1. Progressive Summarization Levels
- **Level 0 (Thesis):** 1-2 sentence core definition or formal claim.
- **Level 1 (Insights):** Curated bullet points, key arguments, and foundational principles.
- **Level 2 (Deep Dive):** Comprehensive explanation, proofs, mechanisms, and historical context.
- **Level 3 (Raw Corpus):** Uncut source excerpts, citations, or transcripts stored in `references/`.

## 2. Note Types & Classifications
- `ConceptIndex`: Structural hub for an academic discipline or subfield (`index.md`).
- `ConceptNote`: Atomic definition of a single phenomenon, theory, or concept.
- `BookNote`: Synthesized literature summary with chapter-level extraction.
- `SynthesisNote`: High-order note linking concepts across multiple disparate domains.
- `EntityNote`: Canonical record of a person, tool, institution, or system.

## 3. Semantic Graph Rules
- Notes link outward to related concepts using standard Obsidian wikilinks: `[[concept-slug|Display Title]]`.
- Every child concept must link back to its parent `[[index|Parent Subfield]]`.
- Aliases must be declared in frontmatter YAML so Brahma's FTS5 engine can resolve synonyms.
"""

METHODOLOGY_ICM = """# Interpretable Context Methodology (ICM) Protocol

The Interpretable Context Methodology replaces monolithic prompt stuffing with filesystem-based agent architecture.

## 1. Core Architecture
- **Knowledge as Filesystem Hierarchy:** Rather than forcing a language model to remember an entire world model, the agent navigates a taxonomized tree (`humanities/`, `formal-sciences/`, etc.).
- **Bounded JIT Context Injection:** A compact local model (`lfm3:1.7b`) only receives the exact notes relevant to the current user turn, preserving context window budget and preventing hallucination.
- **Advisory Skill SOPs:** Task skills inject procedural step-by-step guidance before the turn, ensuring reliable execution without bloating resident system instructions.
- **Append-Only Queues:** New, unverified knowledge lands in `_inbox/` for human review before promotion to the canonical spine.
"""


def get_default_vault_dir() -> Path:
    """Return default target vault directory."""
    return Path.home() / "Documents" / "Obsidian" / "Brahma Vault"


def scaffold_vault_structure(vault_dir: Path) -> Dict[str, Path]:
    """Create directory structure and methodology/template files."""
    knowledge_dir = vault_dir / "wiki" / "library" / "knowledge"
    inbox_dir = knowledge_dir / "_inbox" / "unsorted"
    templates_dir = knowledge_dir / "_templates"
    taxonomy_dir = knowledge_dir / "_taxonomy"
    methodology_dir = vault_dir / "wiki" / "library" / "methodology"
    references_dir = vault_dir / "wiki" / "library" / "references"

    for d in (knowledge_dir, inbox_dir, templates_dir, taxonomy_dir, methodology_dir, references_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 1. Write templates
    (templates_dir / "ConceptNote.md").write_text(TEMPLATE_CONCEPT_NOTE.strip(), encoding="utf-8")
    (templates_dir / "BookNote.md").write_text(TEMPLATE_BOOK_NOTE.strip(), encoding="utf-8")
    (templates_dir / "SynthesisNote.md").write_text(TEMPLATE_SYNTHESIS_NOTE.strip(), encoding="utf-8")

    # 2. Write methodology
    (methodology_dir / "OKF-Protocol.md").write_text(METHODOLOGY_OKF.strip(), encoding="utf-8")
    (methodology_dir / "ICM-Protocol.md").write_text(METHODOLOGY_ICM.strip(), encoding="utf-8")

    # 3. Copy canonical taxonomy.json into vault
    repo_tax_file = _REPO_ROOT / "config" / "taxonomy.json"
    if repo_tax_file.is_file():
        (taxonomy_dir / "taxonomy.json").write_text(
            repo_tax_file.read_text(encoding="utf-8"), encoding="utf-8"
        )

    # 4. Write Root Vault README
    readme_text = """# Brahma AI — Second Brain Vault

Welcome to your Brahma AI Second Brain. This vault is organized using the **Interpretable Context Methodology (ICM)** and the **Open Knowledge Framework (OKF v0.2)**.

## Directory Layout
- `wiki/library/knowledge/`: Canonical academic taxonomy spine (Humanities, Social Sciences, Natural Sciences, Formal Sciences, Applied Sciences).
- `wiki/library/knowledge/_inbox/`: Staging area for new book extractions and unverified notes.
- `wiki/library/knowledge/_templates/`: Standard markdown templates for new concepts and book notes.
- `wiki/library/methodology/`: Architecture specs explaining the OKF and ICM protocols.
- `wiki/library/references/`: Raw reference documents, bibliographies, and source extracts.

## Operating with Brahma AI
- Notes are automatically retrieved by Brahma AI during chat turns and presented to the model as grounded context.
- Use `python tools/ingest_multimodal.py <file>` to convert documents and extract diagrams.
- Use `python tools/extract_knowledge.py <file>` to extract and place atomic concepts into this vault.
"""
    (vault_dir / "README.md").write_text(readme_text.strip(), encoding="utf-8")

    return {
        "vault_dir": vault_dir,
        "knowledge_dir": knowledge_dir,
        "inbox_dir": inbox_dir,
        "templates_dir": templates_dir,
        "taxonomy_dir": taxonomy_dir,
    }


def seed_canonical_nodes(vault_dir: Path, progress_cb: Optional[Any] = None) -> int:
    """Seed all 122 canonical taxonomy spine nodes with index.md and wikipedia.md."""
    knowledge_dir = vault_dir / "wiki" / "library" / "knowledge"
    taxonomy = load_taxonomy(vault_dir)
    cache_dir = get_cache_dir()

    children_map: Dict[str, List[Dict[str, Any]]] = {}
    for node in taxonomy:
        pid = node.get("parent")
        if pid:
            children_map.setdefault(pid, []).append(node)

    seeded_count = 0
    today = datetime.date.today().isoformat()

    for idx, node in enumerate(taxonomy):
        node_id = node["id"]
        title = node.get("title", node_id.split("/")[-1].replace("-", " ").title())
        node_dir = knowledge_dir / node_id
        node_dir.mkdir(parents=True, exist_ok=True)

        children = children_map.get(node_id, [])

        # Check local cache for Wikipedia article
        safe_name = re.sub(r"[^\w-]", "_", title).lower()
        title_cache_file = cache_dir / f"by_title_{safe_name}.json"
        article_data: Dict[str, Any] = {}
        if title_cache_file.is_file():
            try:
                article_data = json.loads(title_cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        if not article_data:
            # Fallback article data from taxonomy scope
            article_data = {
                "title": title,
                "extract": node.get("scope", f"The field of {title}."),
                "source_url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                "revid": 0,
                "fetched_at": today,
            }

        # Seed canonical node with index.md and wikipedia.md
        seed_node(vault_dir, node, article_data, children)

        seeded_count += 1
        if progress_cb:
            progress_cb(seeded_count, len(taxonomy), node_id)

    return seeded_count


def configure_brahma_vault(vault_dir: Path) -> Path:
    """Write configuration to Brahma AI's per-user second brain config."""
    cfg_file = second_brain.config_path()
    cfg_file.parent.mkdir(parents=True, exist_ok=True)

    current_cfg: Dict[str, Any] = {}
    if cfg_file.is_file():
        try:
            current_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    current_cfg["vault"] = str(vault_dir.resolve())
    current_cfg["enabled"] = True

    cfg_file.write_text(json.dumps(current_cfg, indent=2), encoding="utf-8")
    return cfg_file


def bootstrap_vault(
    vault_dir: Optional[Path] = None,
    rebuild_index: bool = True,
    configure_app: bool = True,
) -> Dict[str, Any]:
    """Execute end-to-end vault bootstrap."""
    target_dir = vault_dir or get_default_vault_dir()
    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Scaffold layout & templates
    scaffold_vault_structure(target_dir)

    # 2. Seed all 122 canonical spine nodes
    seeded = seed_canonical_nodes(target_dir)

    # 3. Configure Brahma AI
    cfg_path = None
    if configure_app:
        cfg_path = configure_brahma_vault(target_dir)

    # 4. Rebuild search index
    index_stats = {}
    if rebuild_index:
        try:
            index_stats = second_brain.sync_shelf(
                vault=target_dir,
                embed=False,
                rebuild=True,
            )
        except Exception as exc:
            index_stats = {"error": str(exc)}

    return {
        "ok": True,
        "vault_dir": str(target_dir),
        "nodes_seeded": seeded,
        "config_updated": str(cfg_path) if cfg_path else None,
        "index_stats": index_stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Brahma AI — Vault Bootstrap & Seeding Utility"
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=None,
        help="Path to create/seed the Obsidian vault (default: ~/Documents/Obsidian/Brahma Vault)",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Do not build the SQLite FTS5 search index after seeding.",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="Do not update %LOCALAPPDATA%/BrahmaAI/second_brain/config.json",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output.",
    )

    args = parser.parse_args()

    print(f"Bootstrapping Brahma AI Second Brain at: {args.vault_dir or get_default_vault_dir()}...")

    res = bootstrap_vault(
        vault_dir=args.vault_dir,
        rebuild_index=not args.no_index,
        configure_app=not args.no_config,
    )

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print("\n[SUCCESS] Vault Bootstrap Completed!")
        print(f"  - Vault Location: {res['vault_dir']}")
        print(f"  - Academic Spine Nodes Seeded: {res['nodes_seeded']} / 122")
        if res.get("config_updated"):
            print(f"  - Brahma AI Configured: {res['config_updated']}")
        if res.get("index_stats"):
            stats = res["index_stats"]
            print(f"  - FTS5 Index Built: {stats.get('pages', 0)} pages, {stats.get('chunks', 0)} chunks")
        print("\nNext steps:")
        print("  1. Open the folder in Obsidian to explore the visual knowledge graph.")
        print("  2. Ingest books with: python tools/ingest_multimodal.py <document>")
        print("  3. Extract knowledge with: python tools/extract_knowledge.py <markdown>")

    return 0


if __name__ == "__main__":
    sys.exit(main())
