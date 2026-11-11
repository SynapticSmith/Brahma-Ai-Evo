"""Brahma Peer Task Federation package.

Implements the transport-neutral Brahma Task Envelope (BTE-1),
state machine, isolated SQLite registry, recursion guards, and client.
"""

from __future__ import annotations

from core.peer.artifacts import (
    PeerArtifactMetadata,
    PeerArtifactStore,
)
from core.peer.capabilities import (
    PeerCapabilityCard,
    matches_requirements,
    score_peer,
    select_best_peer,
)
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
from core.peer.scheduler import (
    ConcurrencyLimitExceededError,
    ReservationLedger,
    build_verifier_task,
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
    "ConcurrencyLimitExceededError",
    "CryptoError",
    "FingerprintMismatchError",
    "IdentityError",
    "MachineBindingMismatchError",
    "NodeIdentity",
    "PairingCoordinator",
    "PairingError",
    "PairingLockoutError",
    "PairingOffer",
    "PeerArtifactMetadata",
    "PeerArtifactStore",
    "PeerCapabilityCard",
    "PeerRegistry",
    "PinnedPeer",
    "ReservationLedger",
    "SasMismatchError",
    "SignatureVerificationError",
    "TERMINAL_STATES",
    "WIRE_STATES",
    "WireState",
    "build_verifier_task",
    "canonical_request_hash",
    "compute_node_id",
    "compute_spki_sha256",
    "derive_sas_and_session_key",
    "ed25519_sign",
    "ed25519_verify",
    "generate_ed25519_keypair",
    "generate_x25519_keypair",
    "is_terminal",
    "matches_requirements",
    "score_peer",
    "select_best_peer",
    "sign_request",
    "validate_transition",
    "verify_request_signature",
]
