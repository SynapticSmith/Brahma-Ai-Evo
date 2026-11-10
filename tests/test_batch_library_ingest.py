"""Tests for Batch Document Ingestion and Knowledge Extraction Orchestrator (§6, §7, §8)."""
from __future__ import annotations

from pathlib import Path

from tools.batch_library_ingest import (
    discover_library_books,
    parse_calibre_opf,
    run_batch_ingestion,
)


def test_parse_calibre_opf(tmp_path: Path):
    opf_content = """<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uuid_id" version="2.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
        <dc:title>Principles of Quantum Optics</dc:title>
        <dc:creator opf:role="aut">Leonard Mandel</dc:creator>
        <dc:subject>Physics</dc:subject>
        <dc:subject>Quantum Optics</dc:subject>
        <dc:description>Comprehensive foundations of optical coherence and quantum fields.</dc:description>
        <dc:identifier opf:scheme="ISBN">9780521561617</dc:identifier>
    </metadata>
</package>
"""
    opf_file = tmp_path / "metadata.opf"
    opf_file.write_text(opf_content, encoding="utf-8")

    meta = parse_calibre_opf(opf_file)
    assert meta.title == "Principles of Quantum Optics"
    assert meta.author == "Leonard Mandel"
    assert "Physics" in meta.tags
    assert meta.isbn == "9780521561617"


def test_discover_library_books(tmp_path: Path):
    lib_dir = tmp_path / "book-library"
    lib_dir.mkdir()

    # Author 1 folder with epub
    author_dir = lib_dir / "Author One" / "Book One (1)"
    author_dir.mkdir(parents=True)
    (author_dir / "book_one.epub").write_text("dummy epub", encoding="utf-8")

    # Author 2 folder with txt and opf
    author2_dir = lib_dir / "Author Two" / "Book Two (2)"
    author2_dir.mkdir(parents=True)
    (author2_dir / "book_two.txt").write_text("dummy text", encoding="utf-8")
    opf = """<package><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Custom Book Title</dc:title></metadata></package>"""
    (author2_dir / "metadata.opf").write_text(opf, encoding="utf-8")

    # Ignored directory
    ignored_dir = lib_dir / "fulltext" / ".raw"
    ignored_dir.mkdir(parents=True)
    (ignored_dir / "ignored.txt").write_text("skip me", encoding="utf-8")

    books = discover_library_books(lib_dir)
    assert len(books) == 2
    titles = [b.title for b in books]
    assert "book_one" in titles
    assert "Custom Book Title" in titles


def test_run_batch_ingestion_dry_run(tmp_path: Path):
    lib_dir = tmp_path / "book-library"
    author_dir = lib_dir / "Author" / "Test (1)"
    author_dir.mkdir(parents=True)
    (author_dir / "test.txt").write_text("Hello world", encoding="utf-8")

    vault_dir = tmp_path / "Vault"
    vault_dir.mkdir()

    res = run_batch_ingestion(library_dir=lib_dir, vault_dir=vault_dir, dry_run=True)
    assert res["ok"] is True
    assert res["books_scanned"] == 1
    assert res["books"][0]["status"] == "dry_run"
