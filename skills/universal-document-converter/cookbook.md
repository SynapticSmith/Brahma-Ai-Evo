# Cookbook: Multi-Format Document Conversion to Clean Markdown

## Objective
Convert binary Office documents (DOCX, PPTX, XLSX), PDFs, and EPUBs into clean GitHub-Flavored Markdown.

## Prerequisites
- Python 3.11+ with `markitdown` or `pandoc`.

## Execution Recipe
1. **Convert File via MarkItDown CLI / Python Engine**:
   ```powershell
   python -c "from markitdown import MarkItDown; md = MarkItDown(); result = md.convert('document.docx'); open('output.md', 'w', encoding='utf-8').write(result.text_content)"
   ```
2. **Batch Ingest Across Document Folders**:
   ```powershell
   Get-ChildItem -Path "docs_raw/" -Include *.pdf, *.docx, *.epub -Recurse | ForEach-Object {
     $dest = $_.FullName -replace '\.[^.]+$', '.md'
     python -c "from markitdown import MarkItDown; open('$dest', 'w', encoding='utf-8').write(MarkItDown().convert('$($_.FullName)').text_content)"
   }
   ```
3. **Format Table Alignments & Strip Page Numbers**:
   Filter out standalone header/footer page numbers and empty lines.
4. **Verification Gate**:
   *Gate: Generated markdown file size > 0; headers `#` and pipe tables `|---|` properly rendered.*
5. **Remediation**:
   If PDF is an image scan, pass to OCR engine: `python tools/ingest_multimodal.py --ocr <pdf_path>`.
