"""Ingest a book (EPUB, TXT, or MD) into the Obsidian second brain.

Extracts text and metadata, formats the note with canonical OKF frontmatter,
saves it to the second-brain source shelf, updates the FTS5 index, and
optionally ingests it into Calyx constellations::

    python tools/ingest_book.py "C:\\path\\to\\book.epub" --author "Ken Lee Wheeler"
    python tools/ingest_book.py "C:\\path\\to\\notes.txt" --domain "humanities/philosophy"
    python tools/ingest_book.py "C:\\path\\to\\essay.md" --sync-calyx
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import calyx_bridge, second_brain  # noqa: E402


def _slugify(text: str) -> str:
    """Convert text into a stable kebab-case filename slug."""
    text = re.sub(r"[^\w\s-]", "", str(text or "")).strip().lower()
    return re.sub(r"[-\s]+", "-", text) or "untitled-book"


def extract_epub(path: Path) -> Tuple[Dict[str, Any], str]:
    """Extract metadata and text chapters from an EPUB file using zipfile + xml."""
    metadata: Dict[str, Any] = {"title": path.stem, "authors": [], "tags": []}
    chapters: List[str] = []

    try:
        with zipfile.ZipFile(path, "r") as z:
            # Find the OPF package file
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

            # Extract HTML/XHTML chapter texts
            for name in sorted(z.namelist()):
                if name.endswith((".xhtml", ".html", ".htm")) and not name.endswith(("nav.xhtml", "toc.xhtml")):
                    try:
                        raw_html = z.read(name).decode("utf-8", "ignore")
                        # Strip script and style blocks
                        raw_html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw_html, flags=re.DOTALL)
                        # Replace block tags with newlines
                        raw_html = re.sub(r"<(p|div|h[1-6]|li|br)[^>]*>", "\n", raw_html)
                        # Strip remaining tags and unescape entities
                        plain = re.sub(r"<[^>]+>", "", raw_html)
                        plain = html.unescape(plain)
                        plain = re.sub(r"[ \t]+", " ", plain)
                        plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
                        if len(plain) > 80:
                            chapters.append(plain)
                    except Exception:
                        pass
    except Exception as exc:
        raise ValueError(f"Failed to read EPUB file: {exc}") from exc

    return metadata, "\n\n---\n\n".join(chapters)


def extract_plain_text(path: Path) -> Tuple[Dict[str, Any], str]:
    """Read a plain text or markdown file."""
    metadata: Dict[str, Any] = {"title": path.stem, "authors": [], "tags": []}
    text = path.read_text(encoding="utf-8", errors="replace")

    # If markdown frontmatter is present, extract it
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm = parts[1]
            body = parts[2].strip()
            for line in fm.splitlines():
                if line.startswith("title:"):
                    metadata["title"] = line.split("title:", 1)[1].strip().strip('"\'')
                elif line.startswith("author:"):
                    author = line.split("author:", 1)[1].strip().strip('"\'')
                    metadata["authors"].append(author)
            return metadata, body

    return metadata, text


def build_okf_note(
    title: str,
    authors: List[str],
    tags: List[str],
    source_path: str,
    body: str,
    domain: str = "",
) -> str:
    """Format the book note with canonical OKF frontmatter."""
    today = datetime.date.today().isoformat()
    all_tags = list(tags)
    if domain:
        for part in domain.split("/"):
            if part and part not in all_tags:
                all_tags.append(_slugify(part))

    author_yaml = json.dumps(authors) if authors else "[]"
    tags_yaml = json.dumps(all_tags) if all_tags else "[]"

    lines = [
        "---",
        "type: BookNote",
        f"title: {json.dumps(title)}",
        f"author: {author_yaml}",
        f"tags: {tags_yaml}",
        f"sources: [{json.dumps(source_path)}]",
        f"created: {today}",
        f"updated: {today}",
        "---",
        "",
        f"# {title}",
        "",
        f"> **Author:** {', '.join(authors) if authors else 'Unknown'}",
        f"> **Source:** `{source_path}`",
        "",
        "## Overview",
        "",
        body.strip(),
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="Path to book file (.epub, .txt, .md)")
    parser.add_argument("--title", help="Override book title.")
    parser.add_argument("--author", action="append", help="Book author(s).")
    parser.add_argument("--tag", action="append", help="Tags/categories.")
    parser.add_argument("--domain", default="", help="Knowledge domain (e.g. 'humanities/philosophy').")
    parser.add_argument("--sync-calyx", action="store_true", help="Also ingest into Calyx constellations.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = parser.parse_args(argv)

    src_file = Path(args.file).resolve()
    if not src_file.is_file():
        print(f"Error: File not found: {src_file}", file=sys.stderr)
        return 1

    vault = second_brain.vault_root()
    if not vault:
        print("Error: No Obsidian vault found. Set BRAHMA_SECOND_BRAIN_VAULT or config.", file=sys.stderr)
        return 2

    # Extract text and metadata
    suffix = src_file.suffix.lower()
    if suffix == ".epub":
        meta, body = extract_epub(src_file)
    elif suffix in (".txt", ".md"):
        meta, body = extract_plain_text(src_file)
    else:
        print(f"Unsupported format: {suffix}. Supported: .epub, .txt, .md", file=sys.stderr)
        return 1

    title = args.title or meta.get("title") or src_file.stem
    authors = args.author if args.author else meta.get("authors", [])
    tags = args.tag if args.tag else meta.get("tags", [])
    slug = _slugify(title)

    # Determine destination in vault
    author_folder = _slugify(authors[0]) if authors else "general"
    if args.domain:
        dest_dir = vault / "wiki" / "library" / "knowledge" / Path(args.domain)
    else:
        dest_dir = vault / "book-library" / author_folder
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_file = dest_dir / f"{slug}.md"
    rel_path = str(dest_file.relative_to(vault)).replace("\\", "/")

    # Compose OKF note
    content = build_okf_note(
        title=title,
        authors=authors,
        tags=tags,
        source_path=str(src_file),
        body=body,
        domain=args.domain,
    )

    dest_file.write_text(content, encoding="utf-8")
    print(f"✓ Wrote book note to: {dest_file}")

    # Re-index second brain
    print("Updating second-brain index...")
    second_brain.sync(rebuild=False, embed=False)

    calyx_res: Optional[Dict[str, Any]] = None
    if args.sync_calyx:
        if calyx_bridge.is_available():
            print("Ingesting into Calyx constellations...")
            calyx_res = calyx_bridge.ingest_note(rel_path, content, anchor=True)
            print(f"✓ Calyx ingested: cx_id={calyx_res.get('cx_id')} anchored={calyx_res.get('anchor', {}).get('status')}")
        else:
            print("Notice: Calyx is not available, skipping Calyx sync.", file=sys.stderr)

    result = {
        "ok": True,
        "title": title,
        "authors": authors,
        "slug": slug,
        "vault_path": rel_path,
        "bytes": len(content),
        "calyx": calyx_res,
    }

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
