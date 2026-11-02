"""Windows DPAPI user-scoped secret storage (Finding 6, WS-7).

Provides DPAPI encryption and decryption for sensitive configuration at rest
(e.g. ``api_keys.json``, ``smart_home.key``) using ``CryptProtectData`` and
``CryptUnprotectData`` from Windows ``crypt32.dll``.

Invariants:
- Uses user scope only (never ``CRYPTPROTECT_LOCAL_MACHINE``), so other local
  user accounts cannot read the ciphertext.
- Sets ``CRYPTPROTECT_UI_FORBIDDEN`` (0x01) to prevent interactive modal prompts.
- Atomic writes via ``core.user_paths.atomic_write_json`` to avoid partial writes.
- 1-release read-only fallback for existing plaintext JSON configs.
- Zero non-stdlib external dependencies (uses standard library ``ctypes``).
"""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
from typing import Any, Optional

from core.user_paths import atomic_write_json, get_user_data_dir

# Windows crypt32 constants
CRYPTPROTECT_UI_FORBIDDEN = 0x01

# DPAPI magic header for raw binary key storage (e.g. smart_home.key)
DPAPI_BINARY_PREFIX = b"BRAHMA_DPAPI\x01:"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def is_dpapi_available() -> bool:
    """Return True if Windows DPAPI (crypt32) is available on the current platform."""
    if sys.platform != "win32":
        return False
    try:
        return hasattr(ctypes, "windll") and hasattr(ctypes.windll, "crypt32")
    except Exception:
        return False


def dpapi_protect(data: bytes, entropy: bytes = b"") -> bytes:
    """Encrypt raw bytes using Windows DPAPI user-scoped encryption."""
    if not is_dpapi_available():
        raise RuntimeError("Windows DPAPI is not available on this platform")

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    data_in = DATA_BLOB(
        len(data),
        ctypes.cast(ctypes.c_char_p(data), ctypes.POINTER(ctypes.c_byte)),
    )

    entropy_blob = None
    if entropy:
        entropy_blob = DATA_BLOB(
            len(entropy),
            ctypes.cast(ctypes.c_char_p(entropy), ctypes.POINTER(ctypes.c_byte)),
        )

    data_out = DATA_BLOB()

    ret = crypt32.CryptProtectData(
        ctypes.byref(data_in),
        None,
        ctypes.byref(entropy_blob) if entropy_blob else None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(data_out),
    )
    if not ret:
        err = kernel32.GetLastError()
        raise OSError(f"CryptProtectData failed with error code {err}")

    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        kernel32.LocalFree(data_out.pbData)


def dpapi_unprotect(data: bytes, entropy: bytes = b"") -> bytes:
    """Decrypt DPAPI user-scoped ciphertext."""
    if not is_dpapi_available():
        raise RuntimeError("Windows DPAPI is not available on this platform")

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    data_in = DATA_BLOB(
        len(data),
        ctypes.cast(ctypes.c_char_p(data), ctypes.POINTER(ctypes.c_byte)),
    )

    entropy_blob = None
    if entropy:
        entropy_blob = DATA_BLOB(
            len(entropy),
            ctypes.cast(ctypes.c_char_p(entropy), ctypes.POINTER(ctypes.c_byte)),
        )

    data_out = DATA_BLOB()

    ret = crypt32.CryptUnprotectData(
        ctypes.byref(data_in),
        None,
        ctypes.byref(entropy_blob) if entropy_blob else None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(data_out),
    )
    if not ret:
        err = kernel32.GetLastError()
        raise OSError(f"CryptUnprotectData failed with error code {err}")

    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        kernel32.LocalFree(data_out.pbData)


def save_secret_json(
    path: str | Path,
    data: dict[str, Any],
    *,
    entropy: bytes = b"",
    encrypt: bool = True,
) -> None:
    """Atomically write ``data`` to ``path`` with DPAPI encryption."""
    target = Path(path)
    if encrypt and is_dpapi_available():
        plain_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        cipher_bytes = dpapi_protect(plain_bytes, entropy=entropy)
        envelope = {
            "_dpapi_encrypted": True,
            "version": 1,
            "ciphertext": base64.b64encode(cipher_bytes).decode("ascii"),
        }
        atomic_write_json(target, envelope)
    else:
        atomic_write_json(target, data)


def load_secret_json(
    path: str | Path,
    *,
    entropy: bytes = b"",
    default: Optional[dict[str, Any]] = None,
    must_exist: bool = False,
    auto_migrate: bool = False,
) -> dict[str, Any]:
    """Load and decrypt a DPAPI-encrypted secret JSON file.

    Maintains 1-release read-only fallback for existing plaintext JSON.
    If ``auto_migrate`` is True and a plaintext file was loaded on a system with
    DPAPI available, the file is automatically rewritten encrypted.
    """
    target = Path(path)
    if not target.is_file():
        if must_exist:
            raise FileNotFoundError(f"Secret file not found: {target}")
        return dict(default) if default is not None else {}

    raw_text = target.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw_text)
    except Exception as exc:
        raise ValueError(f"Failed to parse secret JSON from {target}: {exc}") from exc

    if isinstance(parsed, dict) and parsed.get("_dpapi_encrypted"):
        if not is_dpapi_available():
            raise RuntimeError(
                f"Cannot decrypt DPAPI-protected secret in {target}: DPAPI not available"
            )
        b64_cipher = str(parsed.get("ciphertext") or "")
        cipher_bytes = base64.b64decode(b64_cipher.encode("ascii"))
        plain_bytes = dpapi_unprotect(cipher_bytes, entropy=entropy)
        decrypted = json.loads(plain_bytes.decode("utf-8"))
        if not isinstance(decrypted, dict):
            raise ValueError(f"Decrypted payload in {target} is not a JSON dict")
        return decrypted

    # Plaintext JSON (1-release read-only fallback)
    if not isinstance(parsed, dict):
        raise ValueError(f"Secret payload in {target} is not a JSON dict")

    if auto_migrate and is_dpapi_available():
        try:
            save_secret_json(target, parsed, entropy=entropy, encrypt=True)
        except Exception:
            pass

    return parsed


def default_api_keys_path() -> Path:
    """Standard path for api_keys.json under user data directory."""
    return get_user_data_dir() / "config" / "api_keys.json"


def load_api_keys(
    path: Optional[str | Path] = None,
    *,
    must_exist: bool = False,
    auto_migrate: bool = False,
) -> dict[str, Any]:
    """Load API keys from user data or specified path, decrypting if encrypted."""
    target = Path(path) if path is not None else default_api_keys_path()
    return load_secret_json(
        target,
        must_exist=must_exist,
        auto_migrate=auto_migrate,
    )


def save_api_keys(
    data: dict[str, Any],
    path: Optional[str | Path] = None,
) -> None:
    """Save API keys to user data or specified path with DPAPI encryption."""
    target = Path(path) if path is not None else default_api_keys_path()
    save_secret_json(target, data, encrypt=True)
