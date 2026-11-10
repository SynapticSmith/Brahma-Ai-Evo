"""Knowledge unit extraction and taxonomy placement pipeline (§7).

Segments fulltext companion books into atomic knowledge units (ConceptNotes,
EntityNotes, SynthesisNotes), classifies them against the academic taxonomy,
places them into the appropriate canonical spine directory (or routes low-confidence
units to `_inbox/`), and optionally anchors them into Calyx provenance chains::

    python tools/extract_knowledge.py "book-library/fulltext/avijja-and-monistic-emanationism.md"
    python tools/extract_knowledge.py --all-books --confidence-threshold 0.70
    python tools/extract_knowledge.py "book-library/fulltext/buddhism-quotes.md" --sync-calyx --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import calyx_bridge, second_brain  # noqa: E402

MIN_UNIT_CHARS = 350
MAX_UNIT_CHARS = 4_000
HIGH_CONFIDENCE_THRESHOLD = 0.70
DRAFT_CONFIDENCE_THRESHOLD = 0.50


def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", str(text or "")).strip().lower()
    slug = re.sub(r"[-\s]+", "-", text).strip("-")
    return slug or "untitled-concept"


def _sha1_text(text: str) -> str:
    norm = " ".join(text.strip().lower().split())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Segmentation (§7.1)
# --------------------------------------------------------------------------- #

def segment_markdown(text: str) -> List[Dict[str, Any]]:
    """Segment a markdown document by headings and paragraph boundaries into 400-4000 char units."""
    lines = text.splitlines()
    sections: List[Dict[str, Any]] = []
    current_heading = "Overview"
    current_path = ["Overview"]
    buffer: List[str] = []

    def flush_section():
        nonlocal buffer
        body = "\n".join(buffer).strip()
        buffer = []
        if not body:
            return

        # If section is too long, split on double newlines
        if len(body) > MAX_UNIT_CHARS:
            paragraphs = body.split("\n\n")
            chunk_buf: List[str] = []
            chunk_len = 0
            sub_idx = 1
            for p in paragraphs:
                p_clean = p.strip()
                if not p_clean:
                    continue
                if chunk_buf and (chunk_len + len(p_clean) + 2 > MAX_UNIT_CHARS):
                    piece = "\n\n".join(chunk_buf).strip()
                    if len(piece) >= MIN_UNIT_CHARS:
                        sections.append({
                            "heading": f"{current_heading} (Part {sub_idx})",
                            "section_path": " > ".join(current_path) + f" > Part {sub_idx}",
                            "body": piece,
                        })
                        sub_idx += 1
                    chunk_buf = [p_clean]
                    chunk_len = len(p_clean)
                else:
                    chunk_buf.append(p_clean)
                    chunk_len += len(p_clean) + 2
            if chunk_buf:
                piece = "\n\n".join(chunk_buf).strip()
                if len(piece) >= MIN_UNIT_CHARS:
                    title_sfx = f" (Part {sub_idx})" if sub_idx > 1 else ""
                    sections.append({
                        "heading": f"{current_heading}{title_sfx}",
                        "section_path": " > ".join(current_path),
                        "body": piece,
                    })
        elif len(body) >= MIN_UNIT_CHARS:
            sections.append({
                "heading": current_heading,
                "section_path": " > ".join(current_path),
                "body": body,
            })

    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            flush_section()
            level = len(match.group(1))
            heading_title = match.group(2).strip()
            # Ignore markdown links in heading
            heading_title = re.sub(r"\[\[(.*?)\]\]", r"\1", heading_title)
            current_heading = heading_title
            # Update hierarchical section path
            if level == 1:
                current_path = [heading_title]
            elif level == 2:
                current_path = [current_path[0], heading_title] if current_path else [heading_title]
            else:
                current_path = current_path[: level - 1] + [heading_title]
            continue
        # Skip YAML frontmatter markers or callout noise
        if line.strip() == "---" and not buffer:
            continue
        buffer.append(line)
    flush_section()
    return sections


# --------------------------------------------------------------------------- #
# Taxonomy Classification & Routing (§7.2, §7.3)
# --------------------------------------------------------------------------- #

def classify_unit(
    unit: Dict[str, Any],
    taxonomy: List[Dict[str, Any]],
    source_tags: Optional[List[str]] = None,
) -> Tuple[str, float, List[str], str]:
    """Score a candidate unit against taxonomy nodes. Returns (best_domain, confidence, also_in, method)."""
    title = unit.get("heading", "")
    body = unit.get("body", "")
    sample_text = f"{title}\n{body[:1000]}".lower()
    heading_lower = title.lower()

    candidates: List[Tuple[str, float, bool]] = []

    for node in taxonomy:
        node_id = str(node.get("id") or "").lower()
        node_title = str(node.get("title") or "").lower()
        aliases = [str(a).lower() for a in node.get("aliases") or []]
        legacy = [str(l).lower() for l in node.get("legacy_domains") or []]
        slug = node_id.split("/")[-1]
        scope = str(node.get("scope") or "").lower()

        score = 0.0
        in_heading = False

        # Heading exact match gives massive signal
        if node_title in heading_lower or slug in heading_lower:
            score += 0.60
            in_heading = True
        elif node_title in sample_text or slug in sample_text:
            score += 0.40

        for alias in aliases:
            if len(alias) > 3 and alias in sample_text:
                score += 0.35
                if alias in heading_lower:
                    in_heading = True
                break

        # Token overlap with scope
        scope_tokens = [w for w in re.findall(r"[a-z0-9_]{3,}", scope) if len(w) > 3]
        if scope_tokens:
            hits = sum(1 for w in scope_tokens if w in sample_text)
            score += min(0.30, hits * 0.05)

        # Source tag affinity
        if source_tags:
            for tag in source_tags:
                tag_parts = [p.lower() for p in re.split(r"[./\-]", tag)]
                if slug in tag_parts or any(l in tag.lower() for l in legacy):
                    score += 0.40
                    break

        if score > 0.25:
            candidates.append((node.get("id"), score, in_heading))

    candidates.sort(key=lambda c: c[1], reverse=True)

    if not candidates:
        return "humanities/philosophy", 0.35, [], "fallback"

    best_node, best_score, in_heading = candidates[0]
    second_score = candidates[1][1] if len(candidates) > 1 else 0.0
    margin = best_score - second_score

    # Normalized confidence calculation (§7.3)
    if in_heading:
        base_conf = 0.85
    elif best_score >= 0.70:
        base_conf = 0.78
    elif best_score >= 0.45:
        base_conf = 0.65
    else:
        base_conf = 0.40 + (best_score * 0.3)

    confidence = min(0.98, base_conf + min(0.12, margin * 0.25))

    also_in = [c[0] for c in candidates[1:3] if c[1] >= 0.40 and c[0] != best_node]
    method = "heading_match" if in_heading else ("lexical_margin" if margin >= 0.20 else "candidate_top")

    return best_node, round(confidence, 2), also_in, method


# --------------------------------------------------------------------------- #
# Note Generation & Placement (§7.4, §7.5)
# --------------------------------------------------------------------------- #

def extract_units_from_file(
    file_path: Path,
    *,
    vault: Optional[Path] = None,
    confidence_threshold: float = HIGH_CONFIDENCE_THRESHOLD,
    draft_threshold: float = DRAFT_CONFIDENCE_THRESHOLD,
    dry_run: bool = False,
    sync_calyx: bool = False,
) -> Dict[str, Any]:
    """Extract, classify, and place knowledge units from one fulltext companion note."""
    src = file_path.resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Source file not found: {src}")

    text = src.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = second_brain._split_frontmatter(text)

    source_title = frontmatter.get("title") or src.stem.replace("-", " ").title()
    source_slug = frontmatter.get("slug") or src.stem
    source_author = frontmatter.get("author") or "Unknown"
    source_tags = []
    if "tags" in frontmatter:
        try:
            val = frontmatter["tags"]
            source_tags = json.loads(val) if val.startswith("[") else [t.strip() for t in val.split(",")]
        except Exception:
            pass

    vault_root = vault or second_brain.vault_root()
    if not vault_root or not vault_root.is_dir():
        raise ValueError("Valid second brain vault is required for placement.")

    knowledge_root = vault_root / "wiki" / "library" / "knowledge"
    inbox_root = knowledge_root / "_inbox" / "unsorted" / source_slug

    taxonomy = second_brain._load_taxonomy()

    sections = segment_markdown(body)
    placed_units: List[Dict[str, Any]] = []
    inboxed_units: List[Dict[str, Any]] = []
    skipped_count = 0

    seen_hashes: Set[str] = set()

    for sec in sections:
        sec_body = sec["body"].strip()
        sec_hash = _sha1_text(sec_body)
        if sec_hash in seen_hashes:
            skipped_count += 1
            continue
        seen_hashes.add(sec_hash)

        domain, confidence, also_in, method = classify_unit(sec, taxonomy, source_tags)
        unit_slug = _slugify(f"{source_slug}-{sec['heading']}")
        first_sentence = (sec_body.split(". ")[0] + ".").strip()

        # Quality check: reviewed only if substantive and confident (§7.6)
        status = "reviewed" if confidence >= confidence_threshold and len(sec_body) >= 500 else "draft"

        note_content = (
            f"---\n"
            f"type: ConceptNote\n"
            f"title: \"{sec['heading']}\"\n"
            f"domain: {domain}\n"
            f"status: {status}\n"
            f"confidence: {confidence}\n"
            f"sources:\n"
            f"  - \"[[book-library/fulltext/{source_slug}#{_slugify(sec['heading'])}]]\"\n"
            f"also_in: {json.dumps(also_in, ensure_ascii=False)}\n"
            f"tags: {json.dumps(source_tags, ensure_ascii=False)}\n"
            f"summary: \"{first_sentence[:240]}\"\n"
            f"---\n\n"
            f"# {sec['heading']}\n\n"
            f"> [!info] Extracted Knowledge Unit\n"
            f"> Source: [[book-library/fulltext/{source_slug}|{source_title}]] ({source_author})\n"
            f"> Section: `{sec['section_path']}`\n"
            f"> Classification: domain=`{domain}`, confidence={confidence} ({method})\n\n"
            f"{sec_body}\n"
        )

        # Placement decision (§7.3)
        if confidence >= draft_threshold:
            target_dir = knowledge_root / Path(domain)
            target_file = target_dir / f"{unit_slug}.md"
            placed_units.append({
                "slug": unit_slug,
                "title": sec["heading"],
                "domain": domain,
                "confidence": confidence,
                "status": status,
                "path": target_file,
                "content": note_content,
            })
        else:
            target_file = inbox_root / f"{unit_slug}.md"
            inboxed_units.append({
                "slug": unit_slug,
                "title": sec["heading"],
                "candidate_domain": domain,
                "confidence": confidence,
                "path": target_file,
                "content": note_content,
            })

    # Commit notes to disk unless dry_run
    if not dry_run:
        for unit in placed_units:
            target = unit["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(unit["content"], encoding="utf-8")

        for unit in inboxed_units:
            target = unit["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(unit["content"], encoding="utf-8")

        # Calyx Grounding Anchor (§7, §12)
        if sync_calyx and calyx_bridge.is_available():
            for unit in placed_units:
                try:
                    rel_p = unit["path"].relative_to(vault_root).as_posix()
                    calyx_bridge.ingest_note(
                        rel_path=rel_p,
                        raw_markdown=unit["content"],
                        vault="obsidian-wiki",
                        anchor=True,
                    )
                except Exception:
                    pass

    return {
        "ok": True,
        "source": src.as_posix(),
        "source_slug": source_slug,
        "sections_total": len(sections),
        "placed": len(placed_units),
        "inboxed": len(inboxed_units),
        "skipped": skipped_count,
        "placed_units": [
            {"slug": u["slug"], "title": u["title"], "domain": u["domain"], "confidence": u["confidence"], "status": u["status"]}
            for u in placed_units
        ],
        "inboxed_units": [
            {"slug": u["slug"], "title": u["title"], "candidate_domain": u["candidate_domain"], "confidence": u["confidence"]}
            for u in inboxed_units
        ],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="Path to fulltext companion markdown file.")
    parser.add_argument("--all-books", action="store_true", help="Process all fulltext companion notes.")
    parser.add_argument("--vault", help="Target second-brain vault root.")
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=HIGH_CONFIDENCE_THRESHOLD,
        help="Confidence floor for reviewed notes (default 0.70).",
    )
    parser.add_argument(
        "--draft-threshold",
        type=float,
        default=DRAFT_CONFIDENCE_THRESHOLD,
        help="Confidence floor for draft placement; below this routes to _inbox/ (default 0.50).",
    )
    parser.add_argument("--sync-calyx", action="store_true", help="Anchor extracted units into Calyx constellations.")
    parser.add_argument("--dry-run", action="store_true", help="Calculate extraction plan without writing notes.")
    parser.add_argument("--json", action="store_true", help="Emit extraction manifest JSON to stdout.")

    args = parser.parse_args(argv)

    vault_path = Path(args.vault) if args.vault else second_brain.vault_root()
    if not vault_path:
        print("Error: second-brain vault not configured.", file=sys.stderr)
        return 2

    files_to_process: List[Path] = []
    if args.all_books:
        fulltext_dir = vault_path / "book-library" / "fulltext"
        if fulltext_dir.is_dir():
            files_to_process = [
                f for f in sorted(fulltext_dir.glob("*.md")) if not f.name.startswith((".", "_")) and f.name != "index.md"
            ]
    elif args.path:
        files_to_process = [Path(args.path)]
    else:
        print("Error: specify a companion file path or use --all-books", file=sys.stderr)
        return 2

    total_placed = 0
    total_inboxed = 0
    all_results: List[Dict[str, Any]] = []

    for f in files_to_process:
        try:
            res = extract_units_from_file(
                f,
                vault=vault_path,
                confidence_threshold=args.confidence_threshold,
                draft_threshold=args.draft_threshold,
                dry_run=args.dry_run,
                sync_calyx=args.sync_calyx,
            )
            all_results.append(res)
            total_placed += res["placed"]
            total_inboxed += res["inboxed"]
            if not args.json:
                print(
                    f"Extracted {res['source_slug']}: {res['placed']} placed, "
                    f"{res['inboxed']} inboxed, {res['skipped']} deduplicated"
                )
        except Exception as exc:
            if not args.json:
                print(f"Error extracting {f}: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps({
            "ok": True,
            "books_processed": len(all_results),
            "total_placed": total_placed,
            "total_inboxed": total_inboxed,
            "results": all_results,
        }, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
