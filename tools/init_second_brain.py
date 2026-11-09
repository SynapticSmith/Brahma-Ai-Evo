"""Initialize and seed a user's Obsidian second brain from the Brahma template.

Copies the canonical OKF + ICM template into the target directory, updates
Brahma's configuration, and builds the initial search index::

    python tools/init_second_brain.py
    python tools/init_second_brain.py --dest "D:\\MyNotes\\ObsidianVault"
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import second_brain  # noqa: E402

TEMPLATE_DIR = ROOT / "templates" / "second_brain"
DEFAULT_DEST = Path.home() / "Documents" / "Obsidian" / "Brahma Vault"


def copy_template(dest: Path, *, force: bool = False) -> int:
    """Copy template files into dest without overwriting existing files unless force=True."""
    copied = 0
    dest.mkdir(parents=True, exist_ok=True)
    for src_path in TEMPLATE_DIR.rglob("*"):
        rel = src_path.relative_to(TEMPLATE_DIR)
        target = dest / rel
        if src_path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif src_path.is_file():
            if target.exists() and not force:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, target)
            copied += 1
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        default=str(DEFAULT_DEST),
        help=f"Target directory for the new Obsidian vault (default: {DEFAULT_DEST})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing template files in target directory.",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip immediate indexing of the new vault.",
    )
    args = parser.parse_args(argv)

    dest = Path(args.dest).expanduser().resolve()
    print(f"Initializing Brahma second brain at:\n  {dest}")

    if not TEMPLATE_DIR.is_dir():
        print(f"Error: Template directory not found at {TEMPLATE_DIR}", file=sys.stderr)
        return 1

    copied = copy_template(dest, force=args.force)
    print(f"Copied {copied} template files/directories.")

    # Update config.json in Brahma's data directory
    cfg_path = second_brain.config_path()
    cfg_data = {}
    if cfg_path.is_file():
        try:
            cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg_data = {}

    cfg_data["vault"] = str(dest)
    cfg_data["enabled"] = True
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Updated second-brain config at:\n  {cfg_path}")

    # Point environment for the remainder of this process
    os.environ["BRAHMA_SECOND_BRAIN_VAULT"] = str(dest)
    second_brain.clear_cache()

    if not args.no_sync:
        print("\nIndexing new vault...")
        stats = second_brain.sync(rebuild=True, embed=False)
        print(
            f"Indexed {stats.get('pages_indexed', 0)} page(s), "
            f"{stats.get('chunks', 0)} chunk(s) in {stats.get('duration_s', 0)}s."
        )

    print("\n✓ Second brain initialization complete!")
    print(f"To open in Obsidian: Choose 'Open folder as vault' and select:\n  {dest}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
