"""
Wheelhouse Builder & Hash Verifier CLI (WS-3 / tools/build_wheelhouse.py).

Inspects deps.lock and verifies pre-compiled binary wheels into a local wheelhouse/
repository for zero-network, hash-verified skill installations.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Ensure repository root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.dep_broker import DEFAULT_LOCKFILE_PATH, parse_lockfile, verify_file_hash


def build_wheelhouse(
    lockfile_path: Path = DEFAULT_LOCKFILE_PATH,
    output_dir: Path = Path("dist/wheelhouse"),
    verify_only: bool = False,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    packages = parse_lockfile(lockfile_path)

    if not packages:
        print(f"[ERROR] No packages found in {lockfile_path}")
        return 1

    print(f"Loaded {len(packages)} locked packages from {lockfile_path}")

    verified = 0
    missing = 0
    mismatches = 0

    for pkg_name, locked in sorted(packages.items()):
        # Check for wheel in output_dir
        pattern = f"{pkg_name.replace('-', '_')}*.whl"
        wheels = list(output_dir.glob(pattern))
        if not wheels:
            missing += 1
            print(f"  [MISSING] {pkg_name}=={locked.version} (no wheel in {output_dir})")
            continue

        matched = False
        for whl in wheels:
            for exp_hash in locked.hashes:
                if verify_file_hash(whl, exp_hash):
                    matched = True
                    break
            if matched:
                break

        if matched:
            verified += 1
            print(f"  [OK]      {pkg_name}=={locked.version} -> {whl.name}")
        else:
            mismatches += 1
            print(f"  [FAIL]    {pkg_name}=={locked.version} (hash mismatch or corrupt wheel)")

    print("-" * 60)
    print(f"Summary: {verified} verified, {missing} missing, {mismatches} hash mismatches.")

    if verify_only:
        return 0 if (missing == 0 and mismatches == 0) else 1

    if missing > 0:
        print("\nTo populate missing binary wheels from PyPI under locked hashes, run:")
        print(f"  py -3.11 -m pip download --only-binary=:all: --require-hashes -r {lockfile_path} -d {output_dir}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and verify local wheelhouse from deps.lock")
    parser.add_argument("--lockfile", type=Path, default=DEFAULT_LOCKFILE_PATH, help="Path to deps.lock")
    parser.add_argument("--output", type=Path, default=Path("dist/wheelhouse"), help="Wheelhouse directory")
    parser.add_argument("--verify-only", action="store_true", help="Only verify existing wheels without instructions")
    args = parser.parse_args()

    exit_code = build_wheelhouse(lockfile_path=args.lockfile, output_dir=args.output, verify_only=args.verify_only)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
