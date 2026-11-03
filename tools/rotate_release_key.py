"""
Release Key Rotation & Revocation Drill Tool (Phase 3, WS-6).

Performs controlled release key rotation:
1. Generates a new Ed25519 signing keypair.
2. Creates a cryptographically signed Key Rotation & Revocation Statement.
3. Tests both old and new keys against ReleaseManager to verify that:
   - Releases signed by the retired key are rejected.
   - Releases signed by the replacement key are accepted.
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
import sys
from typing import Dict, Tuple

# Ensure repository root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.plugin_sign import generate_keypair, sign_manifest
from core.release_manager import ReleaseManager, ReleaseValidationError


def create_key_rotation_statement(
    old_priv_hex: str,
    old_pub_hex: str,
    new_priv_hex: str,
    new_pub_hex: str,
    reason: str = "Scheduled security rotation drill",
) -> Dict[str, str]:
    """Generates a cryptographic statement dual-signed by old and new keys certifying rotation."""
    statement = {
        "statement_type": "KEY_ROTATION",
        "retired_key": old_pub_hex,
        "replacement_key": new_pub_hex,
        "reason": reason,
        "rotated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    sig_old = sign_manifest(old_priv_hex, statement)
    sig_new = sign_manifest(new_priv_hex, statement)

    statement["signature_retired_key"] = sig_old
    statement["signature_replacement_key"] = sig_new
    return statement


def execute_rotation_drill(
    old_priv_hex: str,
    old_pub_hex: str,
    output_dir: Path,
) -> Tuple[bool, str, str]:
    """Executes a complete key rotation drill and validates rejection of the retired key."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Generate replacement keypair
    new_priv, new_pub = generate_keypair()

    # 2. Dual-signed rotation statement
    statement = create_key_rotation_statement(
        old_priv_hex=old_priv_hex,
        old_pub_hex=old_pub_hex,
        new_priv_hex=new_priv,
        new_pub_hex=new_pub,
    )
    statement_file = output_dir / "key_rotation_statement.json"
    statement_file.write_text(json.dumps(statement, indent=2), encoding="utf-8")

    # 3. Simulate release manager verification with updated trust set
    # Manager now trusts new_pub and excludes old_pub
    mgr = ReleaseManager(
        releases_dir=output_dir / "releases",
        trusted_keys={new_pub},
        policy="auto",
        is_git_clone=False,
    )

    test_manifest = {
        "product": "BrahmaEvo",
        "version": "1.9.9",
        "channel": "stable",
        "commit": "drill12345",
        "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "min_updater": "1.0.0",
        "artifacts": [],
    }

    # Verify that signing with old key fails
    bad_sig = sign_manifest(old_priv_hex, test_manifest)
    try:
        mgr.stage_release(test_manifest, bad_sig, {})
        return False, new_priv, new_pub
    except ReleaseValidationError:
        pass  # Expected: retired key was rejected

    # Verify that signing with new key succeeds
    good_sig = sign_manifest(new_priv, test_manifest)
    try:
        staged = mgr.stage_release(test_manifest, good_sig, {})
        if staged.exists():
            return True, new_priv, new_pub
    except Exception as exc:
        print(f"Drill failed on new key verification: {exc}")
        return False, new_priv, new_pub

    return False, new_priv, new_pub


def main() -> int:
    parser = argparse.ArgumentParser(description="Brahma Release Key Rotation Drill")
    parser.add_argument("--old-key", required=True, help="Current hex-encoded Ed25519 private key")
    parser.add_argument("--old-pub", required=True, help="Current hex-encoded Ed25519 public key")
    parser.add_argument("--output-dir", default="build/key_rotation", help="Directory for rotation artifacts")

    args = parser.parse_args()

    print("[Drill] Initiating release key rotation drill...")
    success, new_priv, new_pub = execute_rotation_drill(
        old_priv_hex=args.old_key,
        old_pub_hex=args.old_pub,
        output_dir=Path(args.output_dir),
    )

    if success:
        print("[Drill] SUCCESS: Key rotation drill passed!")
        print(f"  New Public Key (PIN IN APP): {new_pub}")
        print(f"  New Private Key (SECURE):   {new_priv[:16]}...")
        return 0
    else:
        print("[Drill] FAILURE: Key rotation drill failed validation checks.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
