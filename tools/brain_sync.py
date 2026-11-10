"""Build or refresh the second-brain index used by automatic retrieval.

Run once after cloning, and again whenever the vault changes a lot::

    python tools/brain_sync.py            # incremental, with embeddings
    python tools/brain_sync.py --status   # what the index currently holds
    python tools/brain_sync.py --rebuild  # throw the index away and start over

The vault path comes from (in order) ``--vault``, ``BRAHMA_SECOND_BRAIN_VAULT``,
``%LOCALAPPDATA%\\BrahmaAI\\second_brain\\config.json``, then
``~/Documents/Obsidian/My Vault``.

Embeddings are optional: ``--no-embed`` builds a keyword-only index in seconds.
With embeddings, the first build embeds every chunk once (several minutes on a
CPU) and later runs only embed changed pages.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import second_brain  # noqa: E402


def _progress(done: int, total: int, rel: str) -> None:
    if done == total or done % 50 == 0:
        width = 24
        filled = int(width * done / max(1, total))
        bar = "#" * filled + "-" * (width - filled)
        sys.stdout.write(f"\r[{bar}] {done}/{total}  {rel[:60]:<60}")
        sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", help="Vault root (overrides config).")
    parser.add_argument("--rebuild", action="store_true", help="Discard and rebuild.")
    parser.add_argument("--no-embed", action="store_true", help="Keyword index only.")
    parser.add_argument("--limit", type=int, help="Index at most N pages.")
    parser.add_argument("--shelf", help="Restrict indexing/backfill to a specific shelf (e.g. wiki/ or wiki/entities).")
    parser.add_argument(
        "--embed-limit",
        type=int,
        help="Embed at most N chunks this run (a CPU needs ~1.7 s per chunk).",
    )
    parser.add_argument(
        "--embed-index",
        action="store_true",
        help="Also embed index and reference pages (default: false, only priority notes).",
    )
    parser.add_argument("--status", action="store_true", help="Print index status.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = parser.parse_args(argv)

    if args.vault:
        import os

        os.environ["BRAHMA_SECOND_BRAIN_VAULT"] = args.vault

    if args.status:
        info = second_brain.status()
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0

    if not second_brain.enabled():
        print(
            "No vault found. Set --vault, BRAHMA_SECOND_BRAIN_VAULT, or "
            f"{second_brain.config_path()}.",
            file=sys.stderr,
        )
        return 2

    started = time.time()
    stats = second_brain.sync(
        rebuild=args.rebuild,
        embed=not args.no_embed,
        embed_index=args.embed_index,
        limit=args.limit,
        embed_limit=args.embed_limit,
        progress=None if args.json else _progress,
        shelf=args.shelf,
    )
    if not args.json:
        sys.stdout.write("\n")
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    if not stats.get("ok"):
        return 1
    print(
        f"\nIndexed {stats['pages_indexed']} page(s), skipped "
        f"{stats['pages_skipped']}, {stats['chunks']} chunk(s), "
        f"{stats['vectors']} vector(s) in {stats['duration_s']}s."
    )
    if stats.get("embed_error"):
        print(
            "Embeddings stopped early; keyword search still works: "
            f"{stats['embed_error']}",
            file=sys.stderr,
        )
    print(f"Total: {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
