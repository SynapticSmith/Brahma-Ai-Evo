#!/usr/bin/env python3
"""Inbox Note Promotion and Taxonomy Placement Engine (§7, §16 Decision 6).

Inspects knowledge notes quarantined in `wiki/library/knowledge/_inbox/unsorted/`,
reviews their extracted content, and promotes them into verified academic taxonomy
spine directories with updated frontmatter status and second-brain indexing.

Usage:
    python tools/promote_inbox.py --list
    python tools/promote_inbox.py --promote <filename> --domain humanities/philosophy/eastern-philosophy/buddhism
    python tools/promote_inbox.py --auto-source the-subject-liberated-in-original-buddhism --domain humanities/philosophy/eastern-philosophy/buddhism
    python tools/promote_inbox.py --auto-source all --domain humanities/philosophy/eastern-philosophy/buddhism
    python tools/promote_inbox.py --dry-run
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent import second_brain

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def get_inbox_dir(vault: Optional[Path] = None) -> Path:
    """Return the absolute path to the inbox unsorted directory."""
    vroot = vault or second_brain.vault_root()
    if not vroot:
        raise ValueError("Could not resolve second brain vault root.")
    return vroot / "wiki" / "library" / "knowledge" / "_inbox" / "unsorted"


def list_inbox_notes(vault: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Scan and list all quarantined notes in the inbox."""
    inbox_dir = get_inbox_dir(vault)
    if not inbox_dir.is_dir():
        return []

    notes: List[Dict[str, Any]] = []
    for md_file in sorted(inbox_dir.rglob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        match = FRONTMATTER_RE.match(content)
        fm: Dict[str, Any] = {}
        if match:
            for line in match.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip().strip("\"'")

        source_folder = md_file.parent.name
        notes.append({
            "name": md_file.name,
            "path": str(md_file),
            "source_slug": source_folder,
            "title": fm.get("title", md_file.stem),
            "domain": fm.get("domain", "humanities/philosophy"),
            "confidence": float(fm.get("confidence", 0.0)),
            "status": fm.get("status", "draft"),
            "size": md_file.stat().st_size,
        })

    return notes


def promote_note(
    note_path: Path,
    target_domain: str,
    vault: Optional[Path] = None,
    dry_run: bool = False,
) -> Tuple[bool, str, Optional[Path]]:
    """Promote one inbox note to the verified taxonomy spine."""
    vroot = vault or second_brain.vault_root()
    if not vroot:
        return False, "Could not resolve vault root.", None

    if not note_path.is_file():
        return False, f"Note file not found: {note_path}", None

    # Validate target domain against taxonomy
    nodes = second_brain._load_taxonomy()
    valid_domains = {n.get("id") or n.get("path") for n in nodes if n.get("id") or n.get("path")}
    if target_domain not in valid_domains:
        # Check if it's an alias or prefix
        normalized = target_domain.replace("_", "-").strip("/")
        if normalized in valid_domains:
            target_domain = normalized
        else:
            return False, f"Invalid target taxonomy domain: '{target_domain}'.", None

    target_dir = vroot / "wiki" / "library" / "knowledge" / target_domain
    target_path = target_dir / note_path.name

    if dry_run:
        return True, f"Would move {note_path.name} -> {target_domain}/", target_path

    # Read, update frontmatter, and write
    content = note_path.read_text(encoding="utf-8")
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Update domain and status in frontmatter
    def update_fm(m: re.Match) -> str:
        fm_text = m.group(1)
        # Update domain
        if re.search(r"^domain:", fm_text, re.MULTILINE):
            fm_text = re.sub(r"^domain:.*$", f"domain: {target_domain}", fm_text, flags=re.MULTILINE)
        else:
            fm_text += f"\ndomain: {target_domain}"

        # Update status
        if re.search(r"^status:", fm_text, re.MULTILINE):
            fm_text = re.sub(r"^status:.*$", "status: reviewed", fm_text, flags=re.MULTILINE)
        else:
            fm_text += "\nstatus: reviewed"

        fm_text += f"\npromoted_at: {now_iso}"
        return f"---\n{fm_text}\n---\n"

    new_content = FRONTMATTER_RE.sub(update_fm, content, count=1)

    target_dir.mkdir(parents=True, exist_ok=True)
    target_path.write_text(new_content, encoding="utf-8")

    # Remove original from inbox
    note_path.unlink()

    # Prune empty parent directory if empty
    parent_dir = note_path.parent
    if parent_dir.is_dir() and not any(parent_dir.iterdir()):
        try:
            parent_dir.rmdir()
        except OSError:
            pass

    return True, f"Promoted {note_path.name} -> {target_domain}/", target_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inbox Note Promotion and Taxonomy Placement Engine (§7, §16 Decision 6)."
    )
    parser.add_argument("--list", action="store_true", help="List all quarantined notes in the inbox.")
    parser.add_argument("--promote", help="Filename or path of a specific note to promote.")
    parser.add_argument("--auto-source", help="Promote all notes from a specific source book slug (or 'all').")
    parser.add_argument("--domain", help="Target academic taxonomy domain (e.g. humanities/philosophy/eastern-philosophy/buddhism).")
    parser.add_argument("--vault", help="Target vault root path.")
    parser.add_argument("--dry-run", action="store_true", help="Preview promotion actions without modifying files.")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON.")

    args = parser.parse_args(argv)
    vault_path = Path(args.vault).resolve() if args.vault else second_brain.vault_root()

    if args.list:
        notes = list_inbox_notes(vault_path)
        if args.json:
            print(json.dumps({"count": len(notes), "notes": notes}, indent=2, ensure_ascii=False))
        else:
            print(f"Quarantined Inbox Notes ({len(notes)}):")
            for n in notes:
                print(f"  - [{n['source_slug']}] {n['name']} (conf: {n['confidence']:.2f}) -> {n['domain']}")
        return 0

    if args.promote:
        if not args.domain:
            print("Error: --domain is required when promoting a note.", file=sys.stderr)
            return 2
        inbox_dir = get_inbox_dir(vault_path)
        # Find matching note
        candidates = list(inbox_dir.rglob(args.promote))
        if not candidates:
            candidates = list(inbox_dir.rglob(f"*{args.promote}*"))
        if not candidates:
            print(f"Error: Note matching '{args.promote}' not found in inbox.", file=sys.stderr)
            return 1
        note_file = candidates[0]
        ok, msg, target = promote_note(note_file, args.domain, vault=vault_path, dry_run=args.dry_run)
        if args.json:
            print(json.dumps({"ok": ok, "message": msg, "target": str(target) if target else None}, indent=2))
        else:
            print(f"[{'OK' if ok else 'FAIL'}] {msg}")
        return 0 if ok else 1

    if args.auto_source:
        if not args.domain:
            print("Error: --domain is required when auto-promoting notes.", file=sys.stderr)
            return 2
        inbox_notes = list_inbox_notes(vault_path)
        source_target = args.auto_source.strip().lower()
        to_promote = [
            n for n in inbox_notes
            if source_target == "all" or source_target in n["source_slug"].lower()
        ]
        if not to_promote:
            print(f"No inbox notes matched source '{args.auto_source}'.")
            return 0

        promoted_count = 0
        results = []
        for n in to_promote:
            p = Path(n["path"])
            ok, msg, target = promote_note(p, args.domain, vault=vault_path, dry_run=args.dry_run)
            if ok:
                promoted_count += 1
            results.append({"name": n["name"], "ok": ok, "message": msg})

        # Resync index if changes made
        if not args.dry_run and promoted_count > 0:
            second_brain.sync(embed=False, rebuild=False)

        if args.json:
            print(json.dumps({"promoted": promoted_count, "results": results}, indent=2, ensure_ascii=False))
        else:
            prefix = "[DRY-RUN] " if args.dry_run else ""
            print(f"{prefix}Promoted {promoted_count}/{len(to_promote)} notes to '{args.domain}'.")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
