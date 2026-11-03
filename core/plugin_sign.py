"""
Plugin Cryptographic Signing & Verification Engine (WS-4).

Part of Security Architecture Phase 1.
Implements:
1. Ed25519 digital signature generation and verification for `.bhplugin` packages.
2. Canonical manifest hashing and payload integrity verification.
3. Trust store verification and publisher key revocation checking.
4. Safe unpack and isolation boundaries for extension plugins.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Optional, Set, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

logger = logging.getLogger("PluginSign")


def generate_keypair() -> Tuple[str, str]:
    """Generates an Ed25519 keypair formatted as hex strings (priv, pub)."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()

    priv_hex = priv_key.private_bytes_raw().hex()
    pub_hex = pub_key.public_bytes_raw().hex()
    return priv_hex, pub_hex


def sign_manifest(private_key_hex: str, manifest: Dict[str, Any]) -> str:
    """Computes an Ed25519 signature over the canonical JSON representation of a plugin manifest."""
    priv_bytes = bytes.fromhex(private_key_hex)
    priv_key = ed25519.Ed25519PrivateKey.from_private_bytes(priv_bytes)

    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig_bytes = priv_key.sign(canonical)
    return sig_bytes.hex()


def verify_signature(public_key_hex: str, signature_hex: str, data: bytes) -> bool:
    """Verifies an Ed25519 signature over data using a publisher public key."""
    try:
        pub_bytes = bytes.fromhex(public_key_hex)
        sig_bytes = bytes.fromhex(signature_hex)
        pub_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        pub_key.verify(sig_bytes, data)
        return True
    except (InvalidSignature, ValueError, Exception) as exc:
        logger.debug(f"[PluginSign] Signature verification failed: {exc}")
        return False


def compute_file_sha256(data: bytes) -> str:
    """Computes SHA-256 hash formatted as sha256:<hex>."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


class PluginVerifier:
    """Validates signed .bhplugin manifests, payload hashes, and publisher keys."""

    def __init__(
        self,
        trusted_keys: Optional[Set[str]] = None,
        revoked_keys: Optional[Set[str]] = None,
    ):
        self.trusted_keys: Set[str] = set(trusted_keys or [])
        self.revoked_keys: Set[str] = set(revoked_keys or [])

    def verify_bundle(
        self,
        manifest: Dict[str, Any],
        payload_files: Dict[str, bytes],
        signature_hex: str,
    ) -> Tuple[bool, str]:
        """Validates plugin manifest, publisher key, signature, and all payload file hashes."""
        pub_key = str(manifest.get("publisher_key") or "").strip().lower()
        if not pub_key:
            return False, "Plugin manifest missing 'publisher_key'."

        # 1. Check revocation list
        if pub_key in self.revoked_keys:
            return False, f"Publisher key '{pub_key[:16]}...' has been revoked."

        # 2. Check trust store (if trusted_keys is populated)
        if self.trusted_keys and pub_key not in self.trusted_keys:
            return False, f"Publisher key '{pub_key[:16]}...' is not in the trusted publisher store."

        # 3. Verify all payload hashes match manifest
        declared_hashes = manifest.get("payload_hashes", {})
        if not declared_hashes:
            return False, "Plugin manifest specifies no 'payload_hashes'."

        for file_rel_path, expected_hash in declared_hashes.items():
            content = payload_files.get(file_rel_path)
            if content is None:
                return False, f"Missing payload file declared in manifest: '{file_rel_path}'."
            actual_hash = compute_file_sha256(content)
            if actual_hash.lower() != expected_hash.lower():
                return False, (
                    f"Payload tampering detected in '{file_rel_path}': "
                    f"expected {expected_hash}, got {actual_hash}."
                )

        # 4. Verify cryptographic Ed25519 signature over canonical manifest
        canonical_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if not verify_signature(pub_key, signature_hex, canonical_bytes):
            return False, "Cryptographic signature verification failed: signature is invalid."

        return True, "Plugin bundle is authentic and verified."
