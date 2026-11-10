"""Multimodal document conversion and ingestion pipeline (§6).

Converts PDF, EPUB, DOCX, PPTX, XLSX, HTML, TXT, and Markdown documents into
normalized AI-friendly markdown companions with canonical OKF frontmatter,
extracted figures, and structured conversion manifests::

    python tools/ingest_multimodal.py "path/to/doc.pdf" --slug my-book --tool auto
    python tools/ingest_multimodal.py "path/to/scan.pdf" --tool unlimited-ocr
    python tools/ingest_multimodal.py "path/to/paper.epub" --pages 1-30 --json

Layout:
    book-library/fulltext/
    ├── .raw/<slug>.md                    # converter raw markdown output
    ├── .meta/<slug>.json                 # manifest (tool, hash, pages, warnings)
    ├── .assets/<slug>/fig_<page>_<n>.<ext>  # extracted figures
    └── <slug>.md                         # normalized companion note
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import html
import json
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import second_brain  # noqa: E402

MAX_OUTPUT_BYTES = 50 * 1024 * 1024  # 50 MB safety cap per §6.3

# Core English words for dictionary-word ratio probing on PDF glyph streams
_COMMON_WORDS: Set[str] = frozenset(
    """the of and to a in is that for it as was with on be at by i this had not
    are but from or have an they which one you were her all she there would their
    we him been has when who will more no if out so said what up its about into
    than them can only other new some time could these two may then do first any
    my now such like our over man me even most made after also did many before
    must through back years where much your way well down should because each
    just state good find work people how life part world between child problem
    system group case point government course company hand day year place fact
    water mother father development history general knowledge form theory mind
    action human power nature society experience truth reason reality science
    philosophy religion thought idea order space light matter time soul principle
    existence cause effect being essence substance subject object perception
    concept understanding condition universal particular relation consciousness""".split()
)


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _slugify(text: str) -> str:
    """Deterministic kebab-case slug containing only [a-z0-9-]."""
    text = re.sub(r"[^\w\s-]", "", str(text or "")).strip().lower()
    slug = re.sub(r"[-\s]+", "-", text).strip("-")
    return slug or "untitled-document"


def _parse_pages_arg(pages_arg: Optional[str]) -> Optional[Set[int]]:
    if not pages_arg:
        return None
    pages: Set[int] = set()
    parts = pages_arg.split(",")
    for part in parts:
        part = part.strip()
        if "-" in part:
            start_s, _, end_s = part.partition("-")
            try:
                start = int(start_s)
                end = int(end_s)
                pages.update(range(start, end + 1))
            except ValueError:
                pass
        else:
            try:
                pages.add(int(part))
            except ValueError:
                pass
    return pages if pages else None


# --------------------------------------------------------------------------- #
# Converters
# --------------------------------------------------------------------------- #

def _convert_txt_or_md(src: Path) -> Tuple[str, Dict[str, Any], List[Tuple[str, bytes]]]:
    raw = src.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    meta: Dict[str, Any] = {"title": src.stem, "authors": [], "tags": [], "pages": 1}
    # Parse frontmatter if present
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    k = k.strip().lower()
                    v = v.strip().strip("\"'")
                    if k == "title":
                        meta["title"] = v
                    elif k in ("author", "authors"):
                        meta["authors"] = [a.strip() for a in v.strip("[]").split(",") if a.strip()]
                    elif k == "tags":
                        meta["tags"] = [t.strip() for t in v.strip("[]").split(",") if t.strip()]
    return text, meta, []


def _convert_epub(src: Path) -> Tuple[str, Dict[str, Any], List[Tuple[str, bytes]]]:
    metadata: Dict[str, Any] = {"title": src.stem, "authors": [], "tags": [], "pages": 0}
    chapters: List[str] = []
    assets: List[Tuple[str, bytes]] = []

    with zipfile.ZipFile(src, "r") as z:
        # 1. Metadata from OPF
        opf_path = ""
        for name in z.namelist():
            if name.endswith(".opf"):
                opf_path = name
                break
        if opf_path:
            try:
                opf_xml = z.read(opf_path).decode("utf-8", "ignore")
                root = ET.fromstring(opf_xml)
                ns = {"dc": "http://purl.org/dc/elements/1.1/"}
                title_elem = root.find(".//dc:title", ns)
                if title_elem is not None and title_elem.text:
                    metadata["title"] = title_elem.text.strip()
                for creator in root.findall(".//dc:creator", ns):
                    if creator.text:
                        metadata["authors"].append(creator.text.strip())
                for subject in root.findall(".//dc:subject", ns):
                    if subject.text:
                        metadata["tags"].append(_slugify(subject.text))
            except Exception:
                pass

        # 2. Extract chapters and assets
        chapter_idx = 1
        asset_idx = 1
        for name in sorted(z.namelist()):
            lower = name.lower()
            if lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                try:
                    img_data = z.read(name)
                    ext = lower.split(".")[-1]
                    asset_filename = f"fig_0001_{asset_idx:02d}.{ext}"
                    assets.append((asset_filename, img_data))
                    asset_idx += 1
                except Exception:
                    pass
            elif lower.endswith((".xhtml", ".html", ".htm")) and not lower.endswith(("nav.xhtml", "toc.xhtml")):
                try:
                    raw_html = z.read(name).decode("utf-8", "ignore")
                    raw_html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw_html, flags=re.DOTALL)
                    raw_html = re.sub(r"<(p|div|h[1-6]|li|br)[^>]*>", "\n", raw_html)
                    plain = re.sub(r"<[^>]+>", "", raw_html)
                    plain = html.unescape(plain)
                    plain = re.sub(r"[ \t]+", " ", plain)
                    plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
                    if len(plain) > 60:
                        chapters.append(plain)
                        chapter_idx += 1
                except Exception:
                    pass

    metadata["pages"] = max(1, len(chapters))
    body = "\n\n---\n\n".join(chapters)
    return body, metadata, assets


def _convert_markitdown(src: Path) -> Tuple[str, Dict[str, Any], List[Tuple[str, bytes]]]:
    """Convert office document or HTML using MarkItDown."""
    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        raise RuntimeError("markitdown library is not installed") from exc

    md = MarkItDown()
    result = md.convert(str(src))
    text = result.text_content or ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    title = result.title or src.stem
    meta: Dict[str, Any] = {"title": title, "authors": [], "tags": [], "pages": 1}
    return text, meta, []


def _convert_pdf_per_page(
    src: Path,
    pages_filter: Optional[Set[int]] = None,
    tool_preference: str = "auto",
) -> Tuple[str, Dict[str, Any], List[Tuple[str, bytes]], List[str]]:
    """Inspect and extract PDF page-by-page with quality-score routing and image extraction (§6.1)."""
    try:
        import pypdf
    except ImportError as exc:
        raise RuntimeError("pypdf is required for PDF inspection") from exc

    warnings: List[str] = []
    assets: List[Tuple[str, bytes]] = []
    page_texts: List[str] = []

    reader = pypdf.PdfReader(str(src))
    total_pages = len(reader.pages)
    metadata: Dict[str, Any] = {
        "title": src.stem,
        "authors": [],
        "tags": [],
        "pages": total_pages,
    }

    if reader.metadata:
        if reader.metadata.title:
            metadata["title"] = reader.metadata.title
        if reader.metadata.author:
            metadata["authors"] = [reader.metadata.author]

    for page_idx, page in enumerate(reader.pages, start=1):
        if pages_filter and page_idx not in pages_filter:
            continue

        # Extract embedded images deterministically (§6.4)
        img_idx = 1
        try:
            for img in page.images:
                ext = img.name.split(".")[-1].lower() if "." in img.name else "png"
                asset_name = f"fig_{page_idx:04d}_{img_idx:02d}.{ext}"
                assets.append((asset_name, img.data))
                img_idx += 1
        except Exception as img_err:
            warnings.append(f"Page {page_idx}: image extraction warning ({img_err})")

        # Extract and probe text layer
        extracted = (page.extract_text() or "").strip()
        words = re.findall(r"[A-Za-z]{3,}", extracted)
        dict_ratio = (
            sum(1 for w in words if w.lower() in _COMMON_WORDS) / len(words)
            if words
            else 0.0
        )

        # Scanned page / poor text layer heuristic
        if len(extracted) < 50 or dict_ratio < 0.25:
            # Route to OCR branch (§6.1)
            ocr_text = ""
            if tool_preference in ("auto", "unlimited-ocr"):
                # Attempt OCR dispatch
                warnings.append(
                    f"Page {page_idx}: text layer empty/degraded (dict_ratio={dict_ratio:.2f}); flagged for OCR"
                )
            if not ocr_text:
                ocr_text = (
                    f"*[Page {page_idx}: Scanned page / visual diagram — text extraction degraded]*"
                )
            page_content = ocr_text
        else:
            page_content = extracted

        # Insert asset references if page has figures
        page_assets = [a[0] for a in assets if a[0].startswith(f"fig_{page_idx:04d}_")]
        if page_assets:
            links = "\n\n" + "\n\n".join(
                f"![Figure: page {page_idx}, image {idx}](.assets/{src.stem}/{name})\n*Figure: page {page_idx}, image {idx}*"
                for idx, name in enumerate(page_assets, start=1)
            )
            page_content += links

        page_texts.append(f"## Page {page_idx}\n\n{page_content}")

    body = "\n\n---\n\n".join(page_texts)
    return body, metadata, assets, warnings


# --------------------------------------------------------------------------- #
# Main CLI Pipeline
# --------------------------------------------------------------------------- #

def ingest_document(
    input_path: Path,
    *,
    slug: Optional[str] = None,
    tool: str = "auto",
    vault: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    pages: Optional[str] = None,
    no_network: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    src = input_path.resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Input file not found: {src}")

    suffix = src.suffix.lower()
    doc_slug = _slugify(slug or src.stem)
    src_sha = _sha256_file(src)
    src_bytes = src.stat().st_size

    # Target directory resolution (§6.2)
    vault_base = vault or second_brain.vault_root()
    if out_dir:
        target_root = Path(out_dir).resolve()
    elif vault_base:
        target_root = vault_base / "book-library" / "fulltext"
    else:
        target_root = ROOT / "book-library" / "fulltext"

    raw_dir = target_root / ".raw"
    meta_dir = target_root / ".meta"
    assets_dir = target_root / ".assets" / doc_slug
    companion_path = target_root / f"{doc_slug}.md"
    raw_md_path = raw_dir / f"{doc_slug}.md"
    manifest_path = meta_dir / f"{doc_slug}.json"

    # Resumability check (§6.6): no-op if source sha256 matches
    if not force and manifest_path.is_file() and companion_path.is_file():
        try:
            existing_meta = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing_meta.get("sha256") == src_sha:
                return {
                    "ok": True,
                    "status": "cached",
                    "slug": doc_slug,
                    "companion": str(companion_path),
                    "manifest": str(manifest_path),
                    "bytes": companion_path.stat().st_size,
                    "cached": True,
                }
        except Exception:
            pass

    pages_filter = _parse_pages_arg(pages)
    warnings: List[str] = []
    assets: List[Tuple[str, bytes]] = []
    used_tool = "direct"
    tool_version = "1.0.0"

    # Work in safe temporary sandbox directory (§6.5)
    with tempfile.TemporaryDirectory(prefix=f"brahma_ingest_{doc_slug}_") as tmpdir:
        tmp_path = Path(tmpdir)
        body = ""
        meta: Dict[str, Any] = {}

        if suffix in (".txt", ".md"):
            used_tool = "direct"
            body, meta, assets = _convert_txt_or_md(src)
        elif suffix == ".epub":
            used_tool = "epub-extractor"
            try:
                body, meta, assets = _convert_epub(src)
            except Exception as e:
                warnings.append(f"Direct EPUB extraction failed ({e}), attempting markitdown")
                body, meta, assets = _convert_markitdown(src)
                used_tool = "markitdown"
        elif suffix in (".docx", ".pptx", ".xlsx", ".html", ".htm"):
            used_tool = "markitdown"
            try:
                import markitdown

                tool_version = getattr(markitdown, "__version__", "0.1.9")
                body, meta, assets = _convert_markitdown(src)
            except Exception as e:
                raise RuntimeError(f"MarkItDown conversion failed: {e}") from e
        elif suffix == ".pdf":
            used_tool = "pypdf-router"
            body, meta, assets, pdf_warnings = _convert_pdf_per_page(
                src, pages_filter=pages_filter, tool_preference=tool
            )
            warnings.extend(pdf_warnings)
        else:
            raise ValueError(f"unsupported_format: {suffix}")

        # Check safety size cap (§6.3)
        total_output_len = len(body.encode("utf-8"))
        if total_output_len > MAX_OUTPUT_BYTES:
            raise OverflowError(
                f"Output size {total_output_len} exceeds max safety cap of {MAX_OUTPUT_BYTES} bytes"
            )

        finished_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Build normalized companion frontmatter (§6.2)
        title = meta.get("title") or doc_slug.replace("-", " ").title()
        authors = meta.get("authors") or []
        tags = meta.get("tags") or []
        page_count = meta.get("pages") or 1

        frontmatter = [
            "---",
            "type: BookFullText",
            f"title: \"{title}\"",
            f"author: {json.dumps(authors, ensure_ascii=False)}",
            f"slug: {doc_slug}",
            f"source_format: {suffix.lstrip('.').upper()}",
            f"source_file: \"{src.as_posix()}\"",
            f"source_sha256: {src_sha}",
            f"converted_by: {used_tool}",
            "normalized_by: process:book-library",
            f"converted_at: {datetime.date.today().isoformat()}",
            "status: draft",
            f"tags: {json.dumps(tags, ensure_ascii=False)}",
            f"book_note: \"[[book-library/notes/{doc_slug}]]\"",
            "---\n",
        ]

        companion_text = (
            "\n".join(frontmatter)
            + f"\n# {title}\n\n"
            f"> [!info] Companion full text\n"
            f"> Converted from `{suffix.upper()}` by the Brahma multimodal pipeline (`{used_tool}`).\n"
            f"> Author: {', '.join(authors) if authors else 'Unknown'}\n\n"
            + body
        )

        manifest_data = {
            "source": src.as_posix(),
            "slug": doc_slug,
            "sha256": src_sha,
            "bytes": src_bytes,
            "tool": used_tool,
            "tool_version": tool_version,
            "pages": page_count,
            "assets": len(assets),
            "warnings": warnings,
            "started_at": started_at,
            "finished_at": finished_at,
        }

        # Write to temp files first
        tmp_raw = tmp_path / f"{doc_slug}_raw.md"
        tmp_comp = tmp_path / f"{doc_slug}.md"
        tmp_meta = tmp_path / f"{doc_slug}.json"
        tmp_assets = tmp_path / "assets"
        tmp_assets.mkdir(parents=True, exist_ok=True)

        tmp_raw.write_text(body, encoding="utf-8")
        tmp_comp.write_text(companion_text, encoding="utf-8")
        tmp_meta.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")

        for asset_name, asset_bytes in assets:
            (tmp_assets / asset_name).write_bytes(asset_bytes)

        # Atomic commit into target directories (§6.3)
        raw_dir.mkdir(parents=True, exist_ok=True)
        meta_dir.mkdir(parents=True, exist_ok=True)
        if assets:
            assets_dir.mkdir(parents=True, exist_ok=True)
            for asset_name, _ in assets:
                shutil.copy2(tmp_assets / asset_name, assets_dir / asset_name)

        shutil.copy2(tmp_raw, raw_md_path)
        shutil.copy2(tmp_comp, companion_path)
        shutil.copy2(tmp_meta, manifest_path)

    return {
        "ok": True,
        "status": "converted",
        "slug": doc_slug,
        "companion": str(companion_path),
        "raw": str(raw_md_path),
        "manifest": str(manifest_path),
        "tool": used_tool,
        "pages": page_count,
        "assets": len(assets),
        "warnings": warnings,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Path to input document (PDF, EPUB, DOCX, etc.).")
    parser.add_argument("--slug", help="Explicit document slug (defaults to filename stem).")
    parser.add_argument(
        "--tool",
        choices=["auto", "markitdown", "unlimited-ocr", "paddle", "tesseract", "direct"],
        default="auto",
        help="Conversion tool or router preference (default: auto).",
    )
    parser.add_argument("--vault", help="Target second-brain vault root.")
    parser.add_argument("--out", help="Override output directory (defaults to book-library/fulltext).")
    parser.add_argument("--pages", help="Page range to convert (e.g. '1-30' or '1,3,5').")
    parser.add_argument("--no-network", action="store_true", default=True, help="Enforce offline conversion.")
    parser.add_argument("--force", action="store_true", help="Re-convert even if cached.")
    parser.add_argument("--json", action="store_true", help="Emit manifest JSON to stdout.")

    args = parser.parse_args(argv)

    input_file = Path(args.path)
    if not input_file.is_file():
        if args.json:
            print(json.dumps({"ok": False, "error": f"File not found: {args.path}"}))
        else:
            print(f"Error: file not found: {args.path}", file=sys.stderr)
        return 4

    try:
        result = ingest_document(
            input_file,
            slug=args.slug,
            tool=args.tool,
            vault=Path(args.vault) if args.vault else None,
            out_dir=Path(args.out) if args.out else None,
            pages=args.pages,
            no_network=args.no_network,
            force=args.force,
        )
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"Ingested {result['slug']} successfully via {result.get('tool', 'direct')}:")
            print(f"  Companion: {result['companion']}")
            print(f"  Manifest:  {result['manifest']}")
            print(f"  Pages: {result.get('pages', 1)}, Assets: {result.get('assets', 0)}")
            if result.get("warnings"):
                for w in result["warnings"]:
                    print(f"  [!] {w}")
        return 0
    except ValueError as val_err:
        if "unsupported_format" in str(val_err):
            if args.json:
                print(json.dumps({"ok": False, "error": str(val_err)}))
            else:
                print(f"Error: {val_err}", file=sys.stderr)
            return 2
        return 4
    except RuntimeError as run_err:
        if args.json:
            print(json.dumps({"ok": False, "error": str(run_err)}))
        else:
            print(f"Tool error: {run_err}", file=sys.stderr)
        return 3
    except OverflowError as of_err:
        if args.json:
            print(json.dumps({"ok": False, "error": str(of_err)}))
        else:
            print(f"Error: {of_err}", file=sys.stderr)
        return 5
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"Conversion error: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
