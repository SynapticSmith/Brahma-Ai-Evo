"""
Tests for Release Key Rotation and Revocation Drill (Phase 3, WS-6).
"""

from core.plugin_sign import generate_keypair, verify_signature
from core.release_manager import canonical_json_bytes
from tools.rotate_release_key import (
    create_key_rotation_statement,
    execute_rotation_drill,
)


def test_create_key_rotation_statement():
    """Verifies generation of dual-signed cryptographic key rotation statement."""
    old_priv, old_pub = generate_keypair()
    new_priv, new_pub = generate_keypair()

    statement = create_key_rotation_statement(
        old_priv_hex=old_priv,
        old_pub_hex=old_pub,
        new_priv_hex=new_priv,
        new_pub_hex=new_pub,
        reason="Scheduled rotation drill",
    )

    assert statement["statement_type"] == "KEY_ROTATION"
    assert statement["retired_key"] == old_pub
    assert statement["replacement_key"] == new_pub

    # Verify both signatures cover the canonical statement minus the signature fields
    clean_statement = {
        "statement_type": statement["statement_type"],
        "retired_key": statement["retired_key"],
        "replacement_key": statement["replacement_key"],
        "reason": statement["reason"],
        "rotated_at": statement["rotated_at"],
    }
    canonical = canonical_json_bytes(clean_statement)

    assert verify_signature(old_pub, statement["signature_retired_key"], canonical) is True
    assert verify_signature(new_pub, statement["signature_replacement_key"], canonical) is True


def test_execute_rotation_drill_acceptance(tmp_path):
    """Acceptance test: simulated release key rotation successfully rejects old key and accepts new."""
    old_priv, old_pub = generate_keypair()

    success, new_priv, new_pub = execute_rotation_drill(
        old_priv_hex=old_priv,
        old_pub_hex=old_pub,
        output_dir=tmp_path / "drill_output",
    )

    assert success is True
    assert new_pub != old_pub
    assert len(new_pub) == 64
    assert (tmp_path / "drill_output" / "key_rotation_statement.json").exists()
