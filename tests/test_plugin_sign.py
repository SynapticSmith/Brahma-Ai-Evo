"""Tests for Plugin Signature Verification and Dev Mode (WS-4)."""

import json
import zipfile
import pytest

from core.plugin_sign import (
    PluginVerifier,
    compute_file_sha256,
    generate_keypair,
    sign_manifest,
    verify_signature,
)
from plugin_manager import PluginManager


@pytest.fixture
def keypair():
    priv_hex, pub_hex = generate_keypair()
    return priv_hex, pub_hex


def test_ed25519_keypair_and_signature_roundtrip(keypair):
    """Verifies Ed25519 key generation and signature roundtrip."""
    priv_hex, pub_hex = keypair
    manifest = {
        "id": "com.brahma.spotify",
        "name": "Spotify Control",
        "version": "1.0.0",
        "publisher_key": pub_hex,
    }
    sig_hex = sign_manifest(priv_hex, manifest)
    assert len(sig_hex) == 128  # 64-byte Ed25519 signature in hex

    canonical_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert verify_signature(pub_hex, sig_hex, canonical_bytes) is True
    assert verify_signature(pub_hex, sig_hex, b"tampered_data") is False


def test_verifier_accepts_valid_signed_bundle(keypair):
    """Verifies that a signed bundle with correct hashes passes verification."""
    priv_hex, pub_hex = keypair
    payload_file = b"print('Hello from verified plugin')\n"
    file_hash = compute_file_sha256(payload_file)

    manifest = {
        "id": "com.brahma.test",
        "name": "Test Plugin",
        "version": "1.0.0",
        "publisher_key": pub_hex,
        "payload_hashes": {
            "payload/main.py": file_hash,
        },
    }
    sig_hex = sign_manifest(priv_hex, manifest)

    verifier = PluginVerifier()
    ok, msg = verifier.verify_bundle(
        manifest=manifest,
        payload_files={"payload/main.py": payload_file},
        signature_hex=sig_hex,
    )
    assert ok is True
    assert "verified" in msg.lower()


def test_verifier_rejects_tampered_payload_file(keypair):
    """Acceptance test: a signed bundle with a mismatched payload hash is rejected."""
    priv_hex, pub_hex = keypair
    payload_file = b"original safe content\n"
    file_hash = compute_file_sha256(payload_file)

    manifest = {
        "id": "com.brahma.tampered",
        "name": "Tampered Plugin",
        "version": "1.0.0",
        "publisher_key": pub_hex,
        "payload_hashes": {
            "payload/main.py": file_hash,
        },
    }
    sig_hex = sign_manifest(priv_hex, manifest)

    # Attacker modified the payload bytes
    tampered_bytes = b"MALICIOUS_INJECTED_CODE = True\n"

    verifier = PluginVerifier()
    ok, msg = verifier.verify_bundle(
        manifest=manifest,
        payload_files={"payload/main.py": tampered_bytes},
        signature_hex=sig_hex,
    )
    assert ok is False
    assert "tampering detected" in msg.lower()


def test_verifier_rejects_revoked_publisher_key(keypair):
    """Acceptance test: a revoked publisher key is rejected."""
    priv_hex, pub_hex = keypair
    payload_file = b"content\n"

    manifest = {
        "id": "com.brahma.revoked",
        "name": "Revoked Plugin",
        "version": "1.0.0",
        "publisher_key": pub_hex,
        "payload_hashes": {"payload/main.py": compute_file_sha256(payload_file)},
    }
    sig_hex = sign_manifest(priv_hex, manifest)

    verifier = PluginVerifier(revoked_keys={pub_hex})
    ok, msg = verifier.verify_bundle(
        manifest=manifest,
        payload_files={"payload/main.py": payload_file},
        signature_hex=sig_hex,
    )
    assert ok is False
    assert "revoked" in msg.lower()


def test_dev_plugins_flag_loads_dev_directory_with_warning(tmp_path, capsys):
    """Acceptance test: --dev-plugins shows a warning and loads dev directory."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    dev_script = plugins_dir / "dev_tool.py"
    dev_script.write_text("plugin = 'loaded_in_dev_mode'\n", encoding="utf-8")

    # 1. Normal launch without allowlist skips it
    pm_normal = PluginManager(tmp_path, dev_mode=False)
    assert pm_normal.load_plugins() == []

    # 2. Dev mode loads with warning
    pm_dev = PluginManager(tmp_path, dev_mode=True)
    loaded = pm_dev.load_dev_plugins()
    assert loaded == ["loaded_in_dev_mode"]

    captured = capsys.readouterr()
    assert "[DEV MODE] WARNING" in captured.out


def test_plugin_manager_loads_signed_bhplugin_zip(tmp_path, keypair):
    """Verifies PluginManager can load and verify a .bhplugin zip bundle."""
    priv_hex, pub_hex = keypair
    payload_bytes = b"plugin = {'name': 'bundle_plugin', 'active': True}\n"

    manifest = {
        "id": "com.brahma.zipped",
        "name": "Zipped Plugin",
        "version": "1.0.0",
        "publisher_key": pub_hex,
        "payload_hashes": {"payload/main.py": compute_file_sha256(payload_bytes)},
    }
    sig_hex = sign_manifest(priv_hex, manifest)

    bundle_path = tmp_path / "test.bhplugin"
    with zipfile.ZipFile(bundle_path, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr("signature.ed25519", sig_hex)
        zf.writestr("payload/main.py", payload_bytes)

    pm = PluginManager(tmp_path)
    ok, manifest_out, msg = pm.load_signed_bundle(bundle_path)
    assert ok is True
    assert manifest_out["id"] == "com.brahma.zipped"
    assert "verified" in msg.lower()
