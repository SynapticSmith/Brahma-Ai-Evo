"""Tests for Windows DPAPI secret storage (WS-7, Finding 6)."""

import json
import sys

from cryptography.fernet import Fernet
import pytest

from core.secret_store import (
    DPAPI_BINARY_PREFIX,
    dpapi_protect,
    dpapi_unprotect,
    is_dpapi_available,
    load_api_keys,
    load_secret_json,
    save_api_keys,
    save_secret_json,
)
from smart_home.storage import CredentialVault


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI requires win32")
def test_dpapi_availability():
    assert is_dpapi_available() is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI requires win32")
def test_dpapi_protect_and_unprotect_roundtrip():
    payload = b"super_secret_token_12345_!@#$"
    ciphertext = dpapi_protect(payload)

    assert ciphertext != payload
    assert len(ciphertext) > len(payload)

    decrypted = dpapi_unprotect(ciphertext)
    assert decrypted == payload


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI requires win32")
def test_dpapi_with_optional_entropy():
    payload = b"api_secret_with_entropy"
    entropy = b"context_entropy_salt"

    ciphertext = dpapi_protect(payload, entropy=entropy)
    decrypted = dpapi_unprotect(ciphertext, entropy=entropy)
    assert decrypted == payload

    # Decrypting with wrong entropy must fail
    with pytest.raises(OSError):
        dpapi_unprotect(ciphertext, entropy=b"wrong_entropy")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI requires win32")
def test_save_and_load_secret_json_dpapi_envelope(tmp_path):
    secret_file = tmp_path / "secret.json"
    data = {
        "gemini_api_key": "AIzaSySecretKey",
        "openrouter_api_key": "sk-or-v1-secret",
        "settings": {"feature_a": True, "limit": 42},
    }

    save_secret_json(secret_file, data)
    assert secret_file.exists()

    # Verify the on-disk file is an encrypted envelope, not plaintext
    raw_disk = json.loads(secret_file.read_text(encoding="utf-8"))
    assert raw_disk.get("_dpapi_encrypted") is True
    assert raw_disk.get("version") == 1
    assert "ciphertext" in raw_disk
    assert "gemini_api_key" not in raw_disk
    assert "AIzaSySecretKey" not in secret_file.read_text(encoding="utf-8")

    # Load through load_secret_json and assert identical recovery
    loaded = load_secret_json(secret_file)
    assert loaded == data


def test_load_secret_json_legacy_plaintext_fallback(tmp_path):
    secret_file = tmp_path / "legacy_api_keys.json"
    legacy_data = {
        "gemini_api_key": "legacy_gemini_key",
        "os_system": "windows",
    }
    secret_file.write_text(json.dumps(legacy_data), encoding="utf-8")

    # Must load cleanly without error (1-release read-only fallback)
    loaded = load_secret_json(secret_file)
    assert loaded == legacy_data

    # File on disk remains plaintext when auto_migrate is False
    on_disk = json.loads(secret_file.read_text(encoding="utf-8"))
    assert on_disk.get("_dpapi_encrypted") is None
    assert on_disk.get("gemini_api_key") == "legacy_gemini_key"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI requires win32")
def test_load_secret_json_auto_migrate(tmp_path):
    secret_file = tmp_path / "legacy_migrate.json"
    legacy_data = {
        "anthropic_api_key": "sk-ant-legacy-key",
        "os_system": "windows",
    }
    secret_file.write_text(json.dumps(legacy_data), encoding="utf-8")

    # Loading with auto_migrate=True must load data AND encrypt file on disk
    loaded = load_secret_json(secret_file, auto_migrate=True)
    assert loaded == legacy_data

    on_disk = json.loads(secret_file.read_text(encoding="utf-8"))
    assert on_disk.get("_dpapi_encrypted") is True
    assert "sk-ant-legacy-key" not in secret_file.read_text(encoding="utf-8")


def test_load_secret_json_missing_file_handling(tmp_path):
    missing_file = tmp_path / "does_not_exist.json"

    assert load_secret_json(missing_file) == {}
    assert load_secret_json(missing_file, default={"k": "v"}) == {"k": "v"}

    with pytest.raises(FileNotFoundError):
        load_secret_json(missing_file, must_exist=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI requires win32")
def test_load_and_save_api_keys_custom_path(tmp_path):
    custom_path = tmp_path / "config" / "api_keys.json"
    data = {"gemini_api_key": "custom_key_123"}

    save_api_keys(data, path=custom_path)
    loaded = load_api_keys(path=custom_path)
    assert loaded == data


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI requires win32")
def test_credential_vault_creates_dpapi_key(tmp_path):
    key_file = tmp_path / "smart_home.key"
    assert not key_file.exists()

    vault = CredentialVault(key_file=key_file)
    assert key_file.exists()

    raw_disk = key_file.read_bytes()
    assert raw_disk.startswith(DPAPI_BINARY_PREFIX)

    payload = {"device_id": "lamp_1", "auth_token": "secret_token_abc"}
    encrypted = vault.encrypt_json(payload)
    assert encrypted != json.dumps(payload)

    decrypted = vault.decrypt_json(encrypted)
    assert decrypted == payload

    # Another instance loading the same key file can decrypt the payload
    vault2 = CredentialVault(key_file=key_file)
    assert vault2.decrypt_json(encrypted) == payload


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI requires win32")
def test_credential_vault_migrates_legacy_plaintext_key(tmp_path):
    key_file = tmp_path / "legacy_smart_home.key"
    legacy_key = Fernet.generate_key()
    key_file.write_bytes(legacy_key)

    # Encrypt a payload using the legacy key with Fernet directly
    fernet_legacy = Fernet(legacy_key)
    payload = {"room": "living_room", "state": "on"}
    encrypted_blob = fernet_legacy.encrypt(json.dumps(payload).encode("utf-8")).decode("utf-8")

    # CredentialVault must load the legacy key, decrypt the payload, and migrate to DPAPI on disk
    vault = CredentialVault(key_file=key_file)
    decrypted = vault.decrypt_json(encrypted_blob)
    assert decrypted == payload

    # Verify the file on disk was migrated to DPAPI format
    migrated_disk = key_file.read_bytes()
    assert migrated_disk.startswith(DPAPI_BINARY_PREFIX)
    assert migrated_disk != legacy_key


def test_main_get_api_key_works_with_dpapi_encrypted_config(tmp_path, monkeypatch):
    import main

    key_file = tmp_path / "api_keys.json"
    save_api_keys({"gemini_api_key": "gemini_secure_val_999"}, path=key_file)

    monkeypatch.setattr(main, "API_CONFIG_PATH", key_file)
    retrieved = main._get_api_key()
    assert retrieved == "gemini_secure_val_999"


def test_main_get_api_key_missing_raises_runtime_error(tmp_path, monkeypatch):
    import main

    missing_file = tmp_path / "missing_api_keys.json"
    monkeypatch.setattr(main, "API_CONFIG_PATH", missing_file)

    with pytest.raises(RuntimeError) as exc_info:
        main._get_api_key()
    assert "Missing" in str(exc_info.value)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI requires win32")
def test_credential_vault_recovers_from_corrupt_key(tmp_path):
    key_file = tmp_path / "smart_home.key"
    key_file.write_bytes(b"corrupted_non_base64_data_that_fails_padding")

    # Vault must not crash; it recovers with a fresh valid key
    vault = CredentialVault(key_file=key_file)
    assert vault._fernet is not None

    payload = {"status": "recovered"}
    enc = vault.encrypt_json(payload)
    dec = vault.decrypt_json(enc)
    assert dec == payload
