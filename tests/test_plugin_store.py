"""
Tests for Plugin Store, Trust Store, and Revocation Engine (WS-4, SEC-09).
"""

import json
from pathlib import Path
import zipfile
import pytest

from core.plugin_sign import (
    compute_file_sha256,
    generate_keypair,
    sign_manifest,
)
from core.plugin_store import (
    PluginInstallationError,
    PluginStore,
)


@pytest.fixture
def store_env(tmp_path):
    store_dir = tmp_path / "plugins_store"
    store = PluginStore(base_dir=store_dir)
    priv_hex, pub_hex = generate_keypair()
    return store, priv_hex, pub_hex, tmp_path


def make_test_bundle(
    tmp_path: Path,
    priv_hex: str,
    pub_hex: str,
    plugin_id: str = "com.brahma.testplugin",
    version: str = "1.0.0",
    payload_content: bytes = b"plugin = {'id': 'test'}\n",
) -> Path:
    bundle_path = tmp_path / f"{plugin_id}-{version}.bhplugin"
    payload_hash = compute_file_sha256(payload_content)

    manifest = {
        "id": plugin_id,
        "name": "Test Plugin",
        "version": version,
        "publisher": "Brahma Security",
        "publisher_key": pub_hex,
        "entry": "payload/main.py:register",
        "capabilities": ["net.outbound:api.example.com:443"],
        "payload_hashes": {
            "payload/main.py": payload_hash,
        },
    }
    sig_hex = sign_manifest(priv_hex, manifest)

    with zipfile.ZipFile(bundle_path, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr("signature.ed25519", sig_hex)
        zf.writestr("payload/main.py", payload_content)

    return bundle_path


def test_plugin_store_initialization(store_env):
    """Verifies that PluginStore establishes required layout and index files."""
    store, _, _, _ = store_env
    assert store.store_dir.exists()
    assert store.index_file.exists()
    assert store.trust_file.exists()
    assert store.revocations_file.exists()
    assert store.get_installed_plugins() == {}


def test_trust_store_management(store_env):
    """Verifies adding, listing, and removing trusted publisher keys."""
    store, _, pub_hex, _ = store_env

    assert store.get_trusted_keys() == set()

    store.add_trusted_publisher(pub_hex, "Verified Partner")
    assert pub_hex in store.get_trusted_keys()

    removed = store.remove_trusted_publisher(pub_hex)
    assert removed is True
    assert pub_hex not in store.get_trusted_keys()


def test_install_valid_bundle_unpacks_and_indexes(store_env):
    """Verifies complete installation, content extraction, and indexing of a verified bundle."""
    store, priv_hex, pub_hex, tmp_path = store_env

    bundle_path = make_test_bundle(tmp_path, priv_hex, pub_hex, "com.brahma.weather", "2.1.0")

    manifest = store.install_bundle(bundle_path)
    assert manifest["id"] == "com.brahma.weather"

    installed = store.get_installed_plugins()
    assert "com.brahma.weather" in installed
    info = installed["com.brahma.weather"]
    assert info["version"] == "2.1.0"
    assert info["publisher_key"] == pub_hex

    target_dir = Path(info["path"])
    assert target_dir.exists()
    assert (target_dir / "plugin.json").exists()
    assert (target_dir / "signature.ed25519").exists()
    assert (target_dir / "payload" / "main.py").exists()


def test_install_bundle_rejects_revoked_key(store_env):
    """Acceptance test: a revoked publisher key in the revocation list is rejected."""
    store, priv_hex, pub_hex, tmp_path = store_env

    store.revoke_publisher(pub_hex, "Compromised developer signing key")

    bundle_path = make_test_bundle(tmp_path, priv_hex, pub_hex, "com.brahma.revoked", "1.0.0")

    with pytest.raises(PluginInstallationError) as exc_info:
        store.install_bundle(bundle_path)
    assert "revoked" in str(exc_info.value).lower()


def test_install_bundle_rejects_revoked_plugin_id(store_env):
    """Acceptance test: a revoked plugin ID in the revocation list is rejected."""
    store, priv_hex, pub_hex, tmp_path = store_env

    store.revoke_plugin("com.brahma.malware", "Known malicious package")

    bundle_path = make_test_bundle(tmp_path, priv_hex, pub_hex, "com.brahma.malware", "1.0.0")

    with pytest.raises(PluginInstallationError) as exc_info:
        store.install_bundle(bundle_path)
    assert "revoked" in str(exc_info.value).lower()


def test_install_bundle_rejects_untrusted_publisher_when_store_populated(store_env):
    """Acceptance test: when trust store is populated, unlisted publisher keys are rejected."""
    store, priv_hex, pub_hex, tmp_path = store_env

    # Trust an unrelated key
    _, other_pub = generate_keypair()
    store.add_trusted_publisher(other_pub, "Other Publisher")

    bundle_path = make_test_bundle(tmp_path, priv_hex, pub_hex, "com.brahma.untrusted", "1.0.0")

    with pytest.raises(PluginInstallationError) as exc_info:
        store.install_bundle(bundle_path)
    assert "not in the trusted publisher store" in str(exc_info.value)


def test_uninstall_plugin_removes_files_and_index(store_env):
    """Verifies that uninstall_plugin removes unpacked payload files and cleans index."""
    store, priv_hex, pub_hex, tmp_path = store_env

    bundle_path = make_test_bundle(tmp_path, priv_hex, pub_hex, "com.brahma.temp", "1.0.0")
    store.install_bundle(bundle_path)
    assert "com.brahma.temp" in store.get_installed_plugins()

    path = store.get_plugin_path("com.brahma.temp")
    assert path is not None and path.exists()

    uninstalled = store.uninstall_plugin("com.brahma.temp")
    assert uninstalled is True
    assert "com.brahma.temp" not in store.get_installed_plugins()
    assert not path.exists()
