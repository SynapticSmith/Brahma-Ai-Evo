"""Batch Document Ingestion and Knowledge Extraction Orchestrator (§6, §7, §8).

Recursively discovers books and documents in a library or Calibre collection,
converts them via `tools/ingest_multimodal.py`, atomizes and extracts knowledge
units via `tools/extract_knowledge.py`, places units into the canonical academic
taxonomy spine (or `_inbox/`), anchors them to Calyx constellations, and refreshes
the second brain search index.

Usage:
    python tools/batch_library_ingest.py --library-dir "C:\\Users\\Admin\\Documents\\Obsidian\\My Vault\\book-library"
    python tools/batch_library_ingest.py --calyx
    python tools/batch_library_ingest.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent import calyx_bridge, second_brain
from tools.extract_knowledge import extract_units_from_file
from tools.ingest_multimodal import _slugify as slugify, ingest_document


@dataclass
class BookMetadata:
    title: str
    author: Optional[str] = None
    file_path: Optional[Path] = None
    domain: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    description: Optional[str] = None
    isbn: Optional[str] = None


def parse_calibre_opf(opf_path: Path) -> BookMetadata:
    """Extract metadata from Calibre metadata.opf XML if present."""
    try:
        tree = ET.parse(opf_path)
        root = tree.getroot()
        ns = {
            "opf": "http://www.idpf.org/2007/opf",
            "dc": "http://purl.org/dc/elements/1.1/",
        }

        title_elem = root.find(".//dc:title", ns)
        title = title_elem.text.strip() if title_elem is not None and title_elem.text else opf_path.parent.name

        creator_elem = root.find(".//dc:creator", ns)
        author = creator_elem.text.strip() if creator_elem is not None and creator_elem.text else None

        desc_elem = root.find(".//dc:description", ns)
        desc = desc_elem.text.strip() if desc_elem is not None and desc_elem.text else None

        tags: List[str] = []
        for subj in root.findall(".//dc:subject", ns):
            if subj.text:
                tags.append(subj.text.strip())

        isbn_elem = root.find(".//dc:identifier[@opf:scheme='ISBN']", ns)
        isbn = isbn_elem.text.strip() if isbn_elem is not None and isbn_elem.text else None

        return BookMetadata(
            title=title,
            author=author,
            description=desc,
            tags=tags,
            isbn=isbn,
        )
    except Exception:
        return BookMetadata(title=opf_path.parent.name)


def discover_library_books(library_dir: Path) -> List[BookMetadata]:
    """Discover all processable book documents across library folders."""
    candidates: List[BookMetadata] = []
    supported_exts = {".epub", ".pdf", ".docx", ".txt", ".md"}

    # Avoid recursing into fulltext/.raw, fulltext/.assets, _scripts, or notes
    skip_dirs = {"fulltext", ".assets", ".raw", "_scripts", ".calnotes", "notes"}

    for path in sorted(library_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix.lower() not in supported_exts:
            continue
        if path.name.lower() in ("readme.md", "claude.md", "index.md", "catalog.md", "_taxonomy.md"):
            continue

        # Look for companion metadata.opf in the same folder
        meta = BookMetadata(title=path.stem, file_path=path)
        opf_file = path.parent / "metadata.opf"
        if opf_file.is_file():
            meta = parse_calibre_opf(opf_file)
            meta.file_path = path

        candidates.append(meta)

    return candidates


def run_batch_ingestion(
    library_dir: Path,
    vault_dir: Optional[Path] = None,
    sync_calyx: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Orchestrate end-to-end batch ingestion across the library."""
    vroot = vault_dir or second_brain.vault_root()
    if not vroot:
        raise ValueError("Could not resolve second brain vault root.")

    fulltext_dir = library_dir / "fulltext"
    fulltext_dir.mkdir(parents=True, exist_ok=True)

    books = discover_library_books(library_dir)
    results: List[Dict[str, Any]] = []

    total_units_extracted = 0
    total_placed_in_spine = 0
    total_placed_in_inbox = 0
    total_calyx_anchors = 0

    for book in books:
        if not book.file_path or not book.file_path.is_file():
            continue

        slug = slugify(book.title)
        book_info: Dict[str, Any] = {
            "title": book.title,
            "author": book.author,
            "file": str(book.file_path),
            "slug": slug,
        }

        if dry_run:
            book_info["status"] = "dry_run"
            results.append(book_info)
            continue

        # Stage 1: Multimodal Ingestion (MarkItDown + Asset Extraction)
        try:
            ingest_document(
                book.file_path,
                slug=slug,
                vault=vroot,
            )
            converted_md = fulltext_dir / f"{slug}.md"
            book_info["converted_md"] = str(converted_md)
            book_info["ingest_status"] = "converted"
        except Exception as exc:
            book_info["ingest_status"] = "failed"
            book_info["error"] = str(exc)
            results.append(book_info)
            continue

        # Stage 2: Knowledge Extraction & Spine Routing
        if converted_md.is_file():
            try:
                extract_res = extract_units_from_file(
                    converted_md,
                    vault=vroot,
                    sync_calyx=sync_calyx,
                )
                placed = int(extract_res.get("placed", 0))
                inbox = int(extract_res.get("inboxed", 0))
                sections = int(extract_res.get("sections_total", 0))

                total_units_extracted += sections
                total_placed_in_spine += placed
                total_placed_in_inbox += inbox
                if sync_calyx and calyx_bridge.is_available():
                    total_calyx_anchors += placed + inbox

                book_info["units_extracted"] = sections
                book_info["placed_in_spine"] = placed
                book_info["placed_in_inbox"] = inbox
                book_info["extract_status"] = "success"
            except Exception as exc:
                book_info["extract_status"] = "failed"
                book_info["error"] = str(exc)

        results.append(book_info)

    # Stage 3: Second Brain Index Re-Sync
    sync_stats = {}
    if not dry_run and total_units_extracted > 0:
        try:
            sync_stats = second_brain.sync(
                embed=False,
                rebuild=False,
            )
        except Exception as exc:
            sync_stats = {"error": str(exc)}

    return {
        "ok": True,
        "library_dir": str(library_dir),
        "books_scanned": len(books),
        "total_units_extracted": total_units_extracted,
        "total_placed_in_spine": total_placed_in_spine,
        "total_placed_in_inbox": total_placed_in_inbox,
        "total_calyx_anchors": total_calyx_anchors,
        "sync_stats": sync_stats,
        "books": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Brahma AI — Batch Library Ingestion & Extraction Orchestrator"
    )
    parser.add_argument(
        "--library-dir",
        type=Path,
        default=Path("C:/Users/Admin/Documents/Obsidian/My Vault/book-library"),
        help="Path to the Calibre / book library directory",
    )
    parser.add_argument(
        "--calyx",
        action="store_true",
        help="Anchor extracted units into Calyx constellation ledger",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and list candidate books without converting or extracting",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON results",
    )

    args = parser.parse_args()

    lib_dir = args.library_dir.resolve()
    if not lib_dir.is_dir():
        print(f"[ERROR] Library directory not found: {lib_dir}")
        return 1

    print(f"Scanning library at: {lib_dir}...")
    res = run_batch_ingestion(
        library_dir=lib_dir,
        sync_calyx=args.calyx,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print("\n=== Batch Ingestion Summary ===")
        print(f"  Books Scanned: {res['books_scanned']}")
        print(f"  Knowledge Units Extracted: {res['total_units_extracted']}")
        print(f"  Placed in Academic Spine: {res['total_placed_in_spine']}")
        print(f"  Routed to Inbox for Review: {res['total_placed_in_inbox']}")
        if args.calyx:
            print(f"  Calyx Constellation Anchors: {res['total_calyx_anchors']}")
        if res.get("sync_stats"):
            st = res["sync_stats"]
            print(f"  Second Brain Index Updated: {st.get('pages', 0)} pages, {st.get('chunks', 0)} chunks")

    return 0


if __name__ == "__main__":
    sys.exit(main())
