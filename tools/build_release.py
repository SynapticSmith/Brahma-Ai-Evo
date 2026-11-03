"""
CLI tool for packaging and generating release manifests (WS-6, SEC-11).

Usage:
  python tools/build_release.py --version 1.1.0 --channel stable --artifact dist/BrahmaEvo.zip --notes https://example.com
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys

# Ensure repository root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.release_manager import compute_file_sha256


def get_current_commit() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build release.json manifest for BrahmaEvo")
    parser.add_argument("--version", required=True, help="Semver release version (e.g. 1.1.0)")
    parser.add_argument("--channel", default="stable", choices=["stable", "beta", "nightly"])
    parser.add_argument("--artifact", action="append", default=[], help="Path to artifact file (can repeat)")
    parser.add_argument("--sbom", default=None, help="Path to SBOM file (e.g. sbom.cdx.json)")
    parser.add_argument("--notes", default=None, help="URL to release notes")
    parser.add_argument("--cohort", default="100%", help="Rollout cohort (e.g. 10%%, 25%%, 100%%)")
    parser.add_argument("--output", default="release.json", help="Output path for manifest")

    args = parser.parse_args()

    artifacts_info = []
    for art_path_str in args.artifact:
        art_path = Path(art_path_str)
        if not art_path.exists():
            print(f"Error: Artifact path does not exist: {art_path}")
            return 1

        size = art_path.stat().st_size
        sha256_hash = compute_file_sha256(art_path)
        artifacts_info.append({
            "name": art_path.name,
            "sha256": sha256_hash,
            "size": size,
        })

    manifest = {
        "product": "BrahmaEvo",
        "version": args.version,
        "channel": args.channel,
        "commit": get_current_commit(),
        "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "min_updater": "1.0.0",
        "artifacts": artifacts_info,
        "sbom": args.sbom,
        "notes_url": args.notes,
        "rollout": {
            "cohort": args.cohort,
            "seed": args.version,
        },
    }

    out_file = Path(args.output)
    out_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Generated {out_file} for version {args.version} ({args.channel}) with {len(artifacts_info)} artifact(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
