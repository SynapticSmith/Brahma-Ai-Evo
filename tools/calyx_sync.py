"""Seed and sync the Calyx grounding engine from the Obsidian second brain.

Ingests curated wiki entities, concepts, synthesis notes, and architecture docs
into Calyx constellations, attaches ground-truth anchors, and verifies the
hash-chained provenance ledger::

    python tools/calyx_sync.py               # sync core curated shelves
    python tools/calyx_sync.py --status      # print Calyx vault status
    python tools/calyx_sync.py --shelf wiki/library/knowledge/humanities/philosophy/buddhism
    python tools/calyx_sync.py --limit 20    # test with 20 notes
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import calyx_bridge, second_brain  # noqa: E402

DEFAULT_SHELFS = (
    "wiki/entities",
    "wiki/concepts",
    "wiki/synthesis",
    "Calyx/calyx",
)


def _collect_pages(vault: Path, shelves: Tuple[str, ...], limit: int | None = None) -> List[Tuple[str, Path]]:
    """Find markdown pages across the requested vault shelves."""
    out: List[Tuple[str, Path]] = []
    for shelf in shelves:
        shelf_dir = vault / shelf
        if not shelf_dir.is_dir():
            continue
        for path in shelf_dir.rglob("*.md"):
            if not path.is_file() or path.name.startswith("."):
                continue
            rel = str(path.relative_to(vault)).replace("\\", "/")
            out.append((rel, path))
            if limit and len(out) >= limit:
                return out
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="Print Calyx vault status.")
    parser.add_argument("--vault-name", default=calyx_bridge.DEFAULT_VAULT_NAME, help="Calyx vault name.")
    parser.add_argument("--shelf", action="append", help="Vault shelf/directory to sync (can repeat).")
    parser.add_argument("--limit", type=int, help="Ingest at most N notes.")
    parser.add_argument("--no-anchor", action="store_true", help="Do not anchor ingested notes.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = parser.parse_args(argv)

    if not calyx_bridge.is_available():
        print(
            "Calyx is not available. Ensure calyx-mcp.exe exists at "
            f"{calyx_bridge.calyx_bin()} or set BRAHMA_CALYX_BIN.",
            file=sys.stderr,
        )
        return 2

    if args.status:
        st = calyx_bridge.status(vault=args.vault_name)
        print(json.dumps(st, indent=2, ensure_ascii=False))
        return 0

    vault_dir = second_brain.vault_root()
    if not vault_dir:
        print("Obsidian vault not found. Set BRAHMA_SECOND_BRAIN_VAULT or config.", file=sys.stderr)
        return 2

    shelves = tuple(args.shelf) if args.shelf else DEFAULT_SHELFS
    pages = _collect_pages(vault_dir, shelves, limit=args.limit)
    if not pages:
        print(f"No markdown pages found in shelves: {shelves}")
        return 0

    started = time.time()
    total = len(pages)
    ingested = 0
    new_count = 0
    errors: list[str] = []

    print(f"Syncing {total} notes from {shelves} into Calyx vault '{args.vault_name}'...")

    for i, (rel, path) in enumerate(pages, start=1):
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            errors.append(f"{rel}: read failed ({exc})")
            continue

        res = calyx_bridge.ingest_note(
            rel,
            content,
            vault=args.vault_name,
            anchor=not args.no_anchor,
        )
        if res.get("ok"):
            ingested += 1
            if res.get("new"):
                new_count += 1
            if not args.json:
                sys.stdout.write(f"\r[{i}/{total}] Ingested {rel[:55]:<55}")
                sys.stdout.flush()
        else:
            errors.append(f"{rel}: {res.get('error')}")

    if not args.json:
        sys.stdout.write("\n")

    # Refresh kernel and verify ledger chain
    st = calyx_bridge.status(vault=args.vault_name)
    duration = time.time() - started

    summary = {
        "ok": len(errors) == 0,
        "total_notes": total,
        "ingested": ingested,
        "new_constellations": new_count,
        "errors": errors[:10],
        "duration_s": round(duration, 2),
        "status": st,
    }

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(
            f"Finished in {duration:.1f}s: {ingested}/{total} ingested "
            f"({new_count} new). Kernel size: {st.get('kernel', {}).get('kernel_size')}, "
            f"Chain: {st.get('chain', {}).get('status')} ({st.get('chain', {}).get('checked')} records verified)."
        )

    return 0 if len(errors) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
