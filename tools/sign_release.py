"""
CLI tool for signing release manifests (WS-6, SEC-11).

Usage:
  python tools/sign_release.py --manifest path/to/release.json --key <priv_key_hex>
  python tools/sign_release.py --generate-key
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Ensure repository root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.plugin_sign import generate_keypair, sign_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Sign a Brahma release manifest")
    parser.add_argument("--manifest", type=str, help="Path to release.json file")
    parser.add_argument("--key", type=str, help="Hex-encoded Ed25519 private key")
    parser.add_argument(
        "--output",
        type=str,
        help="Optional output path for signature (default: <manifest>.sig)",
    )
    parser.add_argument(
        "--generate-key",
        action="store_true",
        help="Generate a new Ed25519 release keypair and exit",
    )

    args = parser.parse_args()

    if args.generate_key:
        priv_hex, pub_hex = generate_keypair()
        print("Generated Ed25519 Release Keypair:")
        print(f"  Private Key (KEEP SECRET): {priv_hex}")
        print(f"  Public Key (PIN IN APP):   {pub_hex}")
        return 0

    if not args.manifest or not args.key:
        print("Error: --manifest and --key are required unless --generate-key is passed.")
        return 1

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"Error: Manifest file not found: {manifest_path}")
        return 1

    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Error reading manifest JSON: {exc}")
        return 1

    sig_hex = sign_manifest(args.key, manifest_data)

    out_path = Path(args.output) if args.output else manifest_path.parent / f"{manifest_path.name}.sig"
    out_path.write_text(sig_hex, encoding="utf-8")
    print(f"Signed {manifest_path.name} -> {out_path.name}")
    print(f"Signature: {sig_hex}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
