---
name: universal-document-converter
description: Universal multi-format document conversion pipeline transforming DOCX, PDF, EPUB, PPTX, and XLSX files into clean, structured Markdown.
metadata:
  domain: multimodal
  tags: [document-converter, anydoc, markitdown, docx, pdf, epub, pptx, xlsx, markdown, multimodal]
triggers:
  - "universal document converter"
  - "convert document to markdown"
  - "extract markdown from docx"
  - "pdf to clean markdown"
  - "epub to markdown converter"
  - "excel table to markdown"
  - "convert office file to markdown"
---

# Universal Document Converter Skill

## Overview
Drawn from `anydoc` and `markitdown`, this skill provides a robust multi-format document conversion pipeline for ingesting proprietary office documents and technical publications into clean, readable Markdown. Binary formats (Microsoft Office `.docx`/`.pptx`/`.xlsx`, Adobe `.pdf`, and electronic books `.epub`) cannot be queried natively by text models. This skill extracts structural headings, tables, bullet lists, image captions, and equations while discarding vendor-specific styling bloat.

## Conversion Invariants
1. **Structural Hierarchy Preservation**: Map Word/ePub styles (Heading 1..4) to standard Markdown `#`, `##`, `###` headers.
2. **Table Grid Alignment**: Convert spreadsheets and Word tables into standard GitHub-Flavored Markdown (GFM) pipe tables with normalized header rows.
3. **Image Asset Extraction**: Extract embedded images and diagrams into an adjacent `assets/` subfolder and insert relative markdown image links (`![Caption](assets/fig_01.png)`).
4. **Clean UTF-8 Sanitization**: Strip non-printable control characters, replace curly quotes with standard ASCII quotes, and preserve mathematical Unicode symbols.

## Conversion Strategy by Format
- **PDF**: Run MarkItDown / PDFMiner text extraction; if scanned images detected, route through OCR.
- **DOCX / PPTX**: Unpack XML structure; map paragraph styles, tables, and slide notes to markdown.
- **XLSX**: Convert each worksheet into a separate markdown table or section.
- **EPUB**: Unpack XHTML chapters in spine order; stitch into unified book markdown.
