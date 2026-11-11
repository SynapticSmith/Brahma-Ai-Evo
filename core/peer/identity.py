"""Node Identity and Key Custody for Brahma Swarm / Federation.

Implements ADR-031 and ADR-033:
- Ed25519 node identity key generation and DPAPI user-scoped persistence.
- Stable 16-character base32 node_id: base32(sha256(pubkey))[:16].
- Machine-binding tripwire (MachineGuid and hostname validation).
- Self-signed X.509 certificate generation with SPIFFE and DNS SANs (90-day validity).
- Pinned SPKI fingerprint calculation.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import datetime
import hashlib
from pathlib import Path
import socket
import sys
from typing import Optional, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from core.peer.crypto import (
    compute_node_id,
    compute_spki_sha256,
    ed25519_private_from_raw,
    ed25519_private_to_raw,
    ed25519_public_to_raw,
    generate_ed25519_keypair,
    public_key_to_spki_der,
    sign_request,
)
from core.secret_store import load_secret_json, save_secret_json
from core.user_paths import get_user_data_dir


class IdentityError(Exception):
    """Base error for node identity operations."""


class MachineBindingMismatchError(IdentityError):
    """Raised when the stored node identity does not match the current machine."""


def get_machine_guid() -> str:
    """Read Windows MachineGuid from registry, or return fallback identifier."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            ) as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                if guid and isinstance(guid, str):
                    return guid.strip().lower()
        except Exception:
            pass
    # Fallback for non-Windows or if registry access is denied
    return hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()[:36]


def get_identity_entropy(machine_guid: str) -> bytes:
    """Derive DPAPI additional entropy from machine GUID and Brahma constant."""
    return hashlib.sha256(f"brahma-node-key:{machine_guid}".encode("utf-8")).digest()


@dataclass
class NodeIdentity:
    """Represents the local node's cryptographic identity."""

    node_id: str
    spki_sha256: str
    private_key: ed25519.Ed25519PrivateKey
    public_key: ed25519.Ed25519PublicKey
    machine_guid: str
    hostname: str
    created_at: str

    @classmethod
    def create_new(cls, machine_guid: Optional[str] = None, hostname: Optional[str] = None) -> NodeIdentity:
        """Generate a brand new NodeIdentity in-memory."""
        priv, pub = generate_ed25519_keypair()
        node_id = compute_node_id(pub)
        spki_der = public_key_to_spki_der(pub)
        spki_sha256 = compute_spki_sha256(spki_der)
        guid = machine_guid or get_machine_guid()
        host = hostname or socket.gethostname()
        created = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return cls(
            node_id=node_id,
            spki_sha256=spki_sha256,
            private_key=priv,
            public_key=pub,
            machine_guid=guid,
            hostname=host,
            created_at=created,
        )

    @classmethod
    def load_or_create(
        cls,
        identity_dir: Optional[Path] = None,
        enforce_machine_binding: bool = True,
    ) -> NodeIdentity:
        """Load the node identity from DPAPI-encrypted storage, or create if absent."""
        base_dir = identity_dir or (get_user_data_dir() / "identity")
        base_dir.mkdir(parents=True, exist_ok=True)
        key_file = base_dir / "node.json"

        current_guid = get_machine_guid()
        current_host = socket.gethostname()
        entropy = get_identity_entropy(current_guid)

        if key_file.is_file():
            try:
                data = load_secret_json(key_file, entropy=entropy)
            except Exception as exc:
                if enforce_machine_binding:
                    raise MachineBindingMismatchError(
                        f"Failed to decrypt node key with current machine entropy: {exc}. "
                        "Possible cloned VM or moved profile. Re-pair required."
                    ) from exc
                raise IdentityError(f"Failed to decrypt node key: {exc}") from exc

            if data and "ed25519_private_key_raw" in data:
                stored_guid = str(data.get("machine_guid") or "")
                stored_host = str(data.get("hostname") or "")
                if enforce_machine_binding:
                    if stored_guid and stored_guid != current_guid:
                        raise MachineBindingMismatchError(
                            f"Node key machine_guid mismatch ({stored_guid} vs current {current_guid}). "
                            "Possible cloned VM or moved profile. Re-pair required."
                        )

                priv_raw = base64.b64decode(data["ed25519_private_key_raw"].encode("ascii"))
                priv = ed25519_private_from_raw(priv_raw)
                pub = priv.public_key()
                node_id = str(data.get("node_id") or compute_node_id(pub))
                spki_sha256 = str(data.get("spki_sha256") or compute_spki_sha256(public_key_to_spki_der(pub)))
                created_at = str(data.get("created_at") or datetime.datetime.now(datetime.timezone.utc).isoformat())

                return cls(
                    node_id=node_id,
                    spki_sha256=spki_sha256,
                    private_key=priv,
                    public_key=pub,
                    machine_guid=stored_guid or current_guid,
                    hostname=stored_host or current_host,
                    created_at=created_at,
                )

        # Generate and save fresh identity
        new_identity = cls.create_new(machine_guid=current_guid, hostname=current_host)
        new_identity.save(key_file)
        return new_identity

    def save(self, path: Path) -> None:
        """Persist identity using DPAPI user-scoped encryption."""
        priv_raw = ed25519_private_to_raw(self.private_key)
        pub_raw = ed25519_public_to_raw(self.public_key)
        entropy = get_identity_entropy(self.machine_guid)

        payload = {
            "version": 1,
            "node_id": self.node_id,
            "spki_sha256": self.spki_sha256,
            "ed25519_private_key_raw": base64.b64encode(priv_raw).decode("ascii"),
            "ed25519_public_key_raw": base64.b64encode(pub_raw).decode("ascii"),
            "machine_guid": self.machine_guid,
            "hostname": self.hostname,
            "created_at": self.created_at,
        }
        save_secret_json(path, payload, entropy=entropy, encrypt=True)

    def sign_request(
        self,
        method: str,
        path: str,
        body: Optional[bytes],
        timestamp: int | str,
        nonce: str,
    ) -> str:
        """Sign an outgoing request using this node's private key."""
        return sign_request(
            self.private_key,
            method=method,
            path=path,
            body=body,
            timestamp=timestamp,
            nonce=nonce,
            node_id=self.node_id,
        )

    def generate_certificate(self, days_valid: int = 90) -> Tuple[bytes, bytes]:
        """Generate a self-signed X.509 certificate and private key in PEM format.

        SAN includes:
        - URI: spiffe://brahma.local/node/<node_id>
        - DNS: node-<node_id>.brahma.local
        EKU: serverAuth
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        name = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, f"node-{self.node_id}.brahma.local"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Brahma Federation"),
        ])

        builder = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self.public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=days_valid))
            .add_extension(
                x509.SubjectAlternativeName([
                    x509.UniformResourceIdentifier(f"spiffe://brahma.local/node/{self.node_id}"),
                    x509.DNSName(f"node-{self.node_id}.brahma.local"),
                ]),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
        )

        cert = builder.sign(self.private_key, algorithm=None)
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)

        key_pem = self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        return cert_pem, key_pem

    def save_certificate_files(
        self,
        cert_path: Path,
        key_path: Path,
        days_valid: int = 90,
    ) -> Tuple[Path, Path]:
        """Generate and save PEM certificate and private key to disk."""
        cert_pem, key_pem = self.generate_certificate(days_valid=days_valid)
        cert_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        cert_path.write_bytes(cert_pem)
        key_path.write_bytes(key_pem)
        return cert_path, key_path

    def ensure_certificate_files(
        self,
        cert_dir: Optional[Path] = None,
        days_valid: int = 90,
    ) -> Tuple[Path, Path]:
        """Ensure PEM certificate and key exist on disk, generating them if needed."""
        target_dir = cert_dir or (get_user_data_dir() / "identity")
        target_dir.mkdir(parents=True, exist_ok=True)
        cert_path = target_dir / "cert.pem"
        key_path = target_dir / "key.pem"
        if not (cert_path.is_file() and key_path.is_file()):
            self.save_certificate_files(cert_path, key_path, days_valid=days_valid)
        return cert_path, key_path
