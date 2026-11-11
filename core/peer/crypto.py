"""Cryptographic primitives for Brahma Peer Swarm / Federation.

Implements ADR-031, ADR-032, and ADR-033:
- Ed25519 signing and verification.
- X25519 ephemeral key exchange and HKDF-SHA256 derivation.
- 6-digit Short Authentication String (SAS) calculation.
- AES-256-GCM authenticated encryption for pairing payloads.
- SubjectPublicKeyInfo (SPKI) SHA-256 fingerprinting.
- Request signature generation and verification.
- SPKI-pinning SSL/TLS verification support.
"""

from __future__ import annotations

import base64
import hashlib
import os
import ssl
from typing import Any, Optional, Tuple
import urllib.parse
import urllib.request

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography import x509


class CryptoError(Exception):
    """Base error for peer cryptographic operations."""


class SignatureVerificationError(CryptoError):
    """Raised when request or payload signature verification fails."""


class CertificatePinningError(CryptoError):
    """Raised when a remote TLS certificate fails SPKI pin verification."""


# ---------------------------------------------------------------------------
# Ed25519 Signatures and Node Identity
# ---------------------------------------------------------------------------

def generate_ed25519_keypair() -> Tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey]:
    """Generate a new Ed25519 private/public keypair."""
    priv = ed25519.Ed25519PrivateKey.generate()
    return priv, priv.public_key()


def ed25519_sign(private_key: ed25519.Ed25519PrivateKey, data: bytes) -> bytes:
    """Sign arbitrary data with an Ed25519 private key."""
    return private_key.sign(data)


def ed25519_verify(public_key: ed25519.Ed25519PublicKey, signature: bytes, data: bytes) -> bool:
    """Verify an Ed25519 signature. Returns True if valid, False otherwise."""
    try:
        public_key.verify(signature, data)
        return True
    except InvalidSignature:
        return False


def ed25519_public_to_raw(public_key: ed25519.Ed25519PublicKey) -> bytes:
    """Export Ed25519 public key as 32 raw bytes."""
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def ed25519_public_from_raw(raw_bytes: bytes) -> ed25519.Ed25519PublicKey:
    """Load an Ed25519 public key from 32 raw bytes."""
    return ed25519.Ed25519PublicKey.from_public_bytes(raw_bytes)


def ed25519_private_to_raw(private_key: ed25519.Ed25519PrivateKey) -> bytes:
    """Export Ed25519 private key as 32 raw bytes."""
    return private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def ed25519_private_from_raw(raw_bytes: bytes) -> ed25519.Ed25519PrivateKey:
    """Load an Ed25519 private key from 32 raw bytes."""
    return ed25519.Ed25519PrivateKey.from_private_bytes(raw_bytes)


def compute_node_id(public_key: ed25519.Ed25519PublicKey) -> str:
    """Compute 16-char base32 node_id: base32(sha256(raw_pubkey))[:16].lower()."""
    raw_pub = ed25519_public_to_raw(public_key)
    digest = hashlib.sha256(raw_pub).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=").lower()[:16]


# ---------------------------------------------------------------------------
# SPKI Fingerprinting & Extraction
# ---------------------------------------------------------------------------

def public_key_to_spki_der(public_key: Any) -> bytes:
    """Serialize any public key to SubjectPublicKeyInfo (SPKI) DER bytes."""
    return public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def compute_spki_sha256(spki_der: bytes) -> str:
    """Compute base64-encoded SHA-256 fingerprint of SubjectPublicKeyInfo DER bytes."""
    digest = hashlib.sha256(spki_der).digest()
    return base64.b64encode(digest).decode("ascii")


def spki_from_certificate_der(cert_der: bytes) -> bytes:
    """Extract SubjectPublicKeyInfo DER bytes from an X.509 certificate in DER format."""
    cert = x509.load_der_x509_certificate(cert_der)
    return public_key_to_spki_der(cert.public_key())


def spki_sha256_from_certificate_der(cert_der: bytes) -> str:
    """Compute SPKI SHA-256 base64 fingerprint directly from DER certificate bytes."""
    return compute_spki_sha256(spki_from_certificate_der(cert_der))


def spki_from_certificate_pem(cert_pem: bytes | str) -> bytes:
    """Extract SubjectPublicKeyInfo DER bytes from an X.509 certificate in PEM format."""
    raw = cert_pem.encode("utf-8") if isinstance(cert_pem, str) else cert_pem
    cert = x509.load_pem_x509_certificate(raw)
    return public_key_to_spki_der(cert.public_key())


# ---------------------------------------------------------------------------
# Request Signing & Canonical Signatures (ADR-033)
# ---------------------------------------------------------------------------

def canonical_request_sign_string(
    method: str,
    path: str,
    body: Optional[bytes],
    timestamp: int | str,
    nonce: str,
    node_id: str,
) -> bytes:
    """Build canonical string for request signing:

    METHOD \\n
    PATH \\n
    sha256(body) \\n
    timestamp \\n
    nonce \\n
    node_id
    """
    body_bytes = body if body is not None else b""
    body_sha256 = hashlib.sha256(body_bytes).hexdigest()
    parts = [
        method.strip().upper(),
        path.strip(),
        body_sha256,
        str(timestamp).strip(),
        nonce.strip(),
        node_id.strip(),
    ]
    return "\n".join(parts).encode("utf-8")


def sign_request(
    private_key: ed25519.Ed25519PrivateKey,
    method: str,
    path: str,
    body: Optional[bytes],
    timestamp: int | str,
    nonce: str,
    node_id: str,
) -> str:
    """Sign a request with the node's Ed25519 private key. Returns base64 signature."""
    sign_bytes = canonical_request_sign_string(method, path, body, timestamp, nonce, node_id)
    sig = private_key.sign(sign_bytes)
    return base64.b64encode(sig).decode("ascii")


def verify_request_signature(
    public_key: ed25519.Ed25519PublicKey,
    signature_b64: str,
    method: str,
    path: str,
    body: Optional[bytes],
    timestamp: int | str,
    nonce: str,
    node_id: str,
) -> bool:
    """Verify a request signature using the sender's Ed25519 public key."""
    try:
        sig = base64.b64decode(signature_b64.encode("ascii"))
        sign_bytes = canonical_request_sign_string(method, path, body, timestamp, nonce, node_id)
        public_key.verify(sig, sign_bytes)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# X25519 Key Exchange, HKDF-SHA256, and SAS Calculation (ADR-032)
# ---------------------------------------------------------------------------

def generate_x25519_keypair() -> Tuple[x25519.X25519PrivateKey, x25519.X25519PublicKey]:
    """Generate ephemeral X25519 keypair for SAS pairing."""
    priv = x25519.X25519PrivateKey.generate()
    return priv, priv.public_key()


def x25519_public_to_raw(public_key: x25519.X25519PublicKey) -> bytes:
    """Export X25519 public key as 32 raw bytes."""
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def x25519_public_from_raw(raw_bytes: bytes) -> x25519.X25519PublicKey:
    """Import X25519 public key from 32 raw bytes."""
    return x25519.X25519PublicKey.from_public_bytes(raw_bytes)


def build_pairing_transcript(
    initiator_id: str,
    callee_id: str,
    initiator_eph_pub: bytes,
    callee_eph_pub: bytes,
    initiator_static_pub: bytes,
    callee_static_pub: bytes,
    nonce: str,
) -> bytes:
    """Construct canonical pairing transcript salt."""
    parts = [
        b"brahma-pair-v1",
        initiator_id.encode("utf-8"),
        callee_id.encode("utf-8"),
        initiator_eph_pub,
        callee_eph_pub,
        initiator_static_pub,
        callee_static_pub,
        nonce.encode("utf-8"),
    ]
    return b"|".join(parts)


def derive_sas_and_session_key(
    shared_secret: bytes,
    transcript: bytes,
) -> Tuple[str, bytes]:
    """Derive 6-digit decimal SAS and 256-bit AES-GCM session key via HKDF-SHA256."""
    # 1. 6-digit SAS derivation
    sas_hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=4,
        salt=transcript,
        info=b"brahma-pair-sas",
    )
    sas_bytes = sas_hkdf.derive(shared_secret)
    sas_int = int.from_bytes(sas_bytes, "big") % 1_000_000
    sas_code = f"{sas_int:06d}"

    # 2. 256-bit AES-GCM session key
    session_hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=transcript,
        info=b"brahma-pair-session",
    )
    session_key = session_hkdf.derive(shared_secret)

    return sas_code, session_key


# ---------------------------------------------------------------------------
# AES-256-GCM Authenticated Encryption for Pairing Channel
# ---------------------------------------------------------------------------

def aes_gcm_encrypt(key: bytes, plaintext: bytes, associated_data: bytes = b"") -> bytes:
    """Encrypt plaintext using AES-256-GCM. Returns nonce (12B) + ciphertext + tag (16B)."""
    if len(key) != 32:
        raise ValueError(f"AES-256 key must be 32 bytes, got {len(key)}")
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data or None)
    return nonce + ciphertext


def aes_gcm_decrypt(key: bytes, payload: bytes, associated_data: bytes = b"") -> bytes:
    """Decrypt payload (nonce [12B] + ciphertext + tag) using AES-256-GCM."""
    if len(key) != 32:
        raise ValueError(f"AES-256 key must be 32 bytes, got {len(key)}")
    if len(payload) < 28:
        raise ValueError("Payload too short for AES-GCM (minimum 12-byte nonce + 16-byte tag)")
    aesgcm = AESGCM(key)
    nonce = payload[:12]
    ciphertext = payload[12:]
    return aesgcm.decrypt(nonce, ciphertext, associated_data or None)


# ---------------------------------------------------------------------------
# SPKI Pinning Transport Support
# ---------------------------------------------------------------------------

class PinnedHTTPSConnection(urllib.request.http.client.HTTPSConnection):
    """HTTPSConnection that validates the remote peer certificate's SPKI hash."""

    def __init__(self, *args: Any, pinned_spki: str = "", **kwargs: Any) -> None:
        self.pinned_spki = pinned_spki.strip()
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        super().connect()
        if not self.pinned_spki:
            return
        sock = getattr(self, "sock", None)
        if not sock:
            raise CertificatePinningError("No socket available after TLS connect")
        cert_der = sock.getpeercert(binary_form=True)
        if not cert_der:
            sock.close()
            raise CertificatePinningError("Remote server did not provide a peer certificate")
        actual_spki_hash = spki_sha256_from_certificate_der(cert_der)
        if actual_spki_hash != self.pinned_spki:
            sock.close()
            raise CertificatePinningError(
                f"SPKI pin mismatch! Expected '{self.pinned_spki}', but server presented '{actual_spki_hash}'"
            )


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """Urllib HTTPSHandler that instantiates PinnedHTTPSConnection."""

    def __init__(self, pinned_spki: str, context: Optional[ssl.SSLContext] = None, **kwargs: Any) -> None:
        self.pinned_spki = pinned_spki
        ctx = context
        if ctx is None:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        super().__init__(context=ctx, **kwargs)

    def https_open(self, req: urllib.request.Request) -> Any:
        def _pinned_factory(host: str, **kwargs: Any) -> PinnedHTTPSConnection:
            return PinnedHTTPSConnection(
                host,
                pinned_spki=self.pinned_spki,
                context=self._context,
                **kwargs,
            )
        return self.do_open(_pinned_factory, req)


def build_pinned_opener(pinned_spki: str) -> urllib.request.OpenerDirector:
    """Build a urllib OpenerDirector enforcing SPKI pinning."""
    handler = PinnedHTTPSHandler(pinned_spki=pinned_spki)
    return urllib.request.build_opener(handler)
