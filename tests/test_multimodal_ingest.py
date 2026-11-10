"""Unit and containment tests for the multimodal document ingestion pipeline (§6)."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from tools import ingest_multimodal


def test_slugify_is_deterministic():
    assert ingest_multimodal._slugify("The Nature of Mind & Consciousness: 2026") == "the-nature-of-mind-consciousness-2026"
    assert ingest_multimodal._slugify("  --Some Title--  ") == "some-title"
    assert ingest_multimodal._slugify("") == "untitled-document"


def test_txt_ingestion(tmp_path: Path):
    doc_path = tmp_path / "notes.txt"
    doc_path.write_text("This is an essay on Buddhist ontology and metaphysics.", encoding="utf-8")
    out_dir = tmp_path / "fulltext"

    result = ingest_multimodal.ingest_document(
        doc_path,
        slug="buddhist-ontology",
        out_dir=out_dir,
    )
    assert result["ok"] is True
    assert result["status"] == "converted"
    assert result["slug"] == "buddhist-ontology"
    assert result["pages"] == 1
    assert result["assets"] == 0

    companion = out_dir / "buddhist-ontology.md"
    raw_md = out_dir / ".raw" / "buddhist-ontology.md"
    manifest = out_dir / ".meta" / "buddhist-ontology.json"

    assert companion.is_file()
    assert raw_md.is_file()
    assert manifest.is_file()

    comp_text = companion.read_text(encoding="utf-8")
    assert "type: BookFullText" in comp_text
    assert "slug: buddhist-ontology" in comp_text
    assert "Buddhist ontology" in comp_text

    meta = json.loads(manifest.read_text(encoding="utf-8"))
    assert meta["slug"] == "buddhist-ontology"
    assert meta["tool"] == "direct"
    assert "sha256" in meta


def test_resumability_skips_identical_source(tmp_path: Path):
    doc_path = tmp_path / "sample.md"
    doc_path.write_text("# Sample Title\n\nSample durable text content.\n", encoding="utf-8")
    out_dir = tmp_path / "fulltext"

    # First run
    res1 = ingest_multimodal.ingest_document(doc_path, slug="sample-doc", out_dir=out_dir)
    assert res1["status"] == "converted"

    # Second run without changes
    res2 = ingest_multimodal.ingest_document(doc_path, slug="sample-doc", out_dir=out_dir)
    assert res2["status"] == "cached"
    assert res2["cached"] is True

    # Forced run re-converts
    res3 = ingest_multimodal.ingest_document(doc_path, slug="sample-doc", out_dir=out_dir, force=True)
    assert res3["status"] == "converted"


def test_epub_ingestion_and_asset_extraction(tmp_path: Path):
    epub_path = tmp_path / "book.epub"
    out_dir = tmp_path / "fulltext"

    # Create synthetic EPUB archive
    with zipfile.ZipFile(epub_path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
            <package xmlns="http://www.idpf.org/2007/opf" version="2.0">
              <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                <dc:title>Test Book Title</dc:title>
                <dc:creator>Test Author</dc:creator>
              </metadata>
            </package>
            """,
        )
        z.writestr(
            "OEBPS/chapter1.xhtml",
            "<html><body><h1>Chapter 1</h1><p>First paragraph of the test book chapter with durable knowledge.</p></body></html>",
        )
        z.writestr("OEBPS/images/diagram.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfakeimagebytes")

    result = ingest_multimodal.ingest_document(
        epub_path,
        slug="test-book",
        out_dir=out_dir,
    )

    assert result["ok"] is True
    assert result["slug"] == "test-book"
    assert result["assets"] == 1

    companion = out_dir / "test-book.md"
    assert companion.is_file()
    comp_text = companion.read_text(encoding="utf-8")
    assert "Test Book Title" in comp_text
    assert "Chapter 1" in comp_text

    manifest = json.loads((out_dir / ".meta" / "test-book.json").read_text(encoding="utf-8"))
    assert manifest["assets"] == 1

    asset_file = out_dir / ".assets" / "test-book" / "fig_0001_01.png"
    assert asset_file.is_file()
    assert asset_file.read_bytes() == b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfakeimagebytes"


def test_cli_unsupported_format(tmp_path: Path):
    bad_file = tmp_path / "script.exe"
    bad_file.write_bytes(b"MZfakebinary")
    rc = ingest_multimodal.main([str(bad_file)])
    assert rc == 2


def test_cli_missing_file():
    rc = ingest_multimodal.main(["nonexistent_file.pdf"])
    assert rc == 4


def test_pdf_ingestion_and_page_filtering(tmp_path: Path):
    import pypdf

    pdf_path = tmp_path / "document.pdf"
    writer = pypdf.PdfWriter()
    # Create 3 blank/minimal pages
    for _ in range(3):
        writer.add_blank_page(width=100, height=100)
    with pdf_path.open("wb") as f:
        writer.write(f)

    out_dir = tmp_path / "fulltext"
    # Convert page 1-2 only
    result = ingest_multimodal.ingest_document(
        pdf_path,
        slug="pdf-doc",
        pages="1-2",
        out_dir=out_dir,
    )
    assert result["ok"] is True
    assert result["slug"] == "pdf-doc"
    assert result["tool"] == "pypdf-router"
    # Blank pages generate warning per §6.1
    assert len(result["warnings"]) >= 1

    companion = out_dir / "pdf-doc.md"
    assert companion.is_file()
    text = companion.read_text(encoding="utf-8")
    assert "## Page 1" in text
    assert "## Page 2" in text
    assert "## Page 3" not in text
