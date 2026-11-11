"""Brahma Peer Task Federation package.

Implements the transport-neutral Brahma Task Envelope (BTE-1),
state machine, isolated SQLite registry, recursion guards, and client.
"""

from __future__ import annotations

from core.peer.crypto import (
    CertificatePinningError,
    CryptoError,
    SignatureVerificationError,
    compute_node_id,
    compute_spki_sha256,
    derive_sas_and_session_key,
    ed25519_sign,
    ed25519_verify,
    generate_ed25519_keypair,
    generate_x25519_keypair,
    sign_request,
    verify_request_signature,
)
from core.peer.identity import (
    IdentityError,
    MachineBindingMismatchError,
    NodeIdentity,
)
from core.peer.model import (
    BTE_VERSION,
    BrahmaTaskError,
    BrahmaTaskRequest,
    BrahmaTaskResult,
    canonical_request_hash,
)
from core.peer.pairing import (
    FingerprintMismatchError,
    PairingCoordinator,
    PairingError,
    PairingLockoutError,
    PairingOffer,
    PeerRegistry,
    PinnedPeer,
    SasMismatchError,
)
from core.peer.state import (
    TERMINAL_STATES,
    WIRE_STATES,
    WireState,
    is_terminal,
    validate_transition,
)

__all__ = [
    "BTE_VERSION",
    "BrahmaTaskError",
    "BrahmaTaskRequest",
    "BrahmaTaskResult",
    "CertificatePinningError",
    "CryptoError",
    "FingerprintMismatchError",
    "IdentityError",
    "MachineBindingMismatchError",
    "NodeIdentity",
    "PairingCoordinator",
    "PairingError",
    "PairingLockoutError",
    "PairingOffer",
    "PeerRegistry",
    "PinnedPeer",
    "SasMismatchError",
    "SignatureVerificationError",
    "TERMINAL_STATES",
    "WIRE_STATES",
    "WireState",
    "canonical_request_hash",
    "compute_node_id",
    "compute_spki_sha256",
    "derive_sas_and_session_key",
    "ed25519_sign",
    "ed25519_verify",
    "generate_ed25519_keypair",
    "generate_x25519_keypair",
    "is_terminal",
    "sign_request",
    "validate_transition",
    "verify_request_signature",
]
