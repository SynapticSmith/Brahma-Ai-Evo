"""Unit and integration tests for Peer Authentication, Identity, SAS Pairing, and Signatures."""

from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from core.peer.crypto import (
    ed25519_public_to_raw,
    generate_ed25519_keypair,
    generate_x25519_keypair,
    verify_request_signature,
    x25519_public_to_raw,
)
from core.peer.identity import MachineBindingMismatchError, NodeIdentity
from core.peer.pairing import (
    FingerprintMismatchError,
    PairingCoordinator,
    PairingLockoutError,
    PairingOffer,
    PeerRegistry,
    PinnedPeer,
)


def test_ed25519_node_identity_creation_and_signing():
    """Verify NodeIdentity generation, node_id stability, and request signing."""
    node = NodeIdentity.create_new(machine_guid="test-machine-guid-1", hostname="test-host-1")
    assert len(node.node_id) == 16
    assert node.node_id.isalnum()
    assert len(node.spki_sha256) > 20

    # Request signing
    sig = node.sign_request("POST", "/tasks", b'{"prompt": "hello"}', 1700000000, "nonce-1")
    assert isinstance(sig, str) and len(sig) > 0

    # Verify signature
    valid = verify_request_signature(
        node.public_key,
        sig,
        "POST",
        "/tasks",
        b'{"prompt": "hello"}',
        1700000000,
        "nonce-1",
        node.node_id,
    )
    assert valid is True

    # Tampered path or body should fail
    tampered = verify_request_signature(
        node.public_key,
        sig,
        "POST",
        "/tasks/other",
        b'{"prompt": "hello"}',
        1700000000,
        "nonce-1",
        node.node_id,
    )
    assert tampered is False


def test_node_identity_dpapi_persistence_and_machine_binding():
    """Verify DPAPI persistence and machine-binding tripwire."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        ident_dir = Path(tmp_dir)
        # Create first identity
        node1 = NodeIdentity.load_or_create(identity_dir=ident_dir, enforce_machine_binding=False)
        assert node1.node_id

        # Reloading returns the exact same identity
        node2 = NodeIdentity.load_or_create(identity_dir=ident_dir, enforce_machine_binding=False)
        assert node2.node_id == node1.node_id
        assert node2.spki_sha256 == node1.spki_sha256

        # Machine binding mismatch should raise
        with pytest.raises(MachineBindingMismatchError):
            # Save an identity with a different machine guid
            node1.machine_guid = "other-foreign-machine-guid"
            node1.save(ident_dir / "node.json")
            NodeIdentity.load_or_create(identity_dir=ident_dir, enforce_machine_binding=True)


def test_sas_pairing_protocol_and_token_exchange(tmp_path: Path):
    """Verify mutual SAS derivation agreement and AES-256-GCM token wrapping."""
    node_a = NodeIdentity.create_new(machine_guid="guid-a", hostname="host-a")
    node_b = NodeIdentity.create_new(machine_guid="guid-b", hostname="host-b")

    # Explicit temp registries: the default would create the operator's real
    # ``%LOCALAPPDATA%\BrahmaAI\peer`` directory just by being constructed.
    coord_a = PairingCoordinator(node_a, registry=PeerRegistry(tmp_path / "peers_a.json"))
    coord_b = PairingCoordinator(node_b, registry=PeerRegistry(tmp_path / "peers_b.json"))

    # 1. A creates offer
    offer = coord_a.create_offer(host="192.168.1.10", port=8000)
    uri = offer.to_uri()
    parsed_offer = PairingOffer.from_uri(uri)
    assert parsed_offer.node_id == node_a.node_id
    assert parsed_offer.spki_sha256 == node_a.spki_sha256

    # 2. Key exchange
    a_eph_priv, a_eph_pub, a_static_pub = coord_a.begin_exchange(parsed_offer, is_initiator=True)
    b_eph_priv, b_eph_pub, b_static_pub = coord_b.begin_exchange(parsed_offer, is_initiator=False)

    # 3. Derive SAS and session keys on both sides
    sas_a, session_key_a = coord_a.complete_key_exchange(
        eph_priv=a_eph_priv,
        peer_node_id=node_b.node_id,
        peer_eph_pub_bytes=b_eph_pub,
        peer_static_pub_bytes=b_static_pub,
        peer_spki_sha256=node_b.spki_sha256,
        nonce=parsed_offer.nonce,
        is_initiator=True,
    )

    sas_b, session_key_b = coord_b.complete_key_exchange(
        eph_priv=b_eph_priv,
        peer_node_id=node_a.node_id,
        peer_eph_pub_bytes=a_eph_pub,
        peer_static_pub_bytes=a_static_pub,
        peer_spki_sha256=node_a.spki_sha256,
        nonce=parsed_offer.nonce,
        is_initiator=False,
    )

    # Both nodes derive the exact same 6-digit SAS and session key!
    assert len(sas_a) == 6
    assert sas_a.isdigit()
    assert sas_a == sas_b
    assert session_key_a == session_key_b

    # 4. Callee B wraps token payload, A decrypts it
    enc_payload = coord_b.wrap_token_payload(
        session_key=session_key_b,
        token="tok_callee_secret_12345",
        profile="worker",
        callee_node_id=node_b.node_id,
        callee_spki_sha256=node_b.spki_sha256,
        callee_endpoint="https://192.168.1.20:8000",
    )

    unwrapped = coord_a.unwrap_token_payload(session_key_a, enc_payload)
    assert unwrapped["token"] == "tok_callee_secret_12345"
    assert unwrapped["profile"] == "worker"
    assert unwrapped["callee_node_id"] == node_b.node_id


def test_pairing_lockout_after_three_failures(tmp_path: Path):
    """Verify 3 failed attempts triggers a 5-minute lockout."""
    node = NodeIdentity.create_new()
    coord = PairingCoordinator(node, registry=PeerRegistry(tmp_path / "peers_lockout.json"))

    offer = coord.create_offer("localhost", 8000)
    eph_priv, _, _ = coord.begin_exchange(offer, is_initiator=True)

    fake_eph = x25519_public_to_raw(generate_x25519_keypair()[1])
    fake_static = generate_ed25519_keypair()[1]

    # Attempt with incorrect claimed node_id
    for _ in range(3):
        with pytest.raises(Exception):
            coord.complete_key_exchange(
                eph_priv=eph_priv,
                peer_node_id="wrong_node_id_12",
                peer_eph_pub_bytes=fake_eph,
                peer_static_pub_bytes=ed25519_public_to_raw(fake_static),
                peer_spki_sha256="fake_fp",
                nonce="nonce1",
                is_initiator=True,
            )

    # 4th attempt must be locked out immediately
    with pytest.raises(PairingLockoutError):
        coord.begin_exchange(offer, is_initiator=True)


def test_peer_registry_no_tofu_after_first_pin():
    """Verify NO TOFU invariant: changed fingerprint is rejected unless explicit re-pair."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        reg_file = Path(tmp_dir) / "peers.json"
        registry = PeerRegistry(reg_file)

        peer1 = PinnedPeer(
            node_id="node-laptop-1",
            name="Laptop",
            endpoint="https://192.168.1.15:8000",
            spki_sha256="fp_original_11111111111111111111",
            token="token1",
            profile="worker",
            paired_at="2026-11-10T00:00:00Z",
        )
        registry.save_peer(peer1)

        # Loading peer matches
        loaded = registry.get_peer("node-laptop-1")
        assert loaded is not None
        assert loaded.spki_sha256 == "fp_original_11111111111111111111"

        # Attempt to save peer with altered fingerprint without re-pair must fail!
        peer_tampered = PinnedPeer(
            node_id="node-laptop-1",
            name="Laptop",
            endpoint="https://192.168.1.15:8000",
            spki_sha256="fp_ATTACKER_2222222222222222222",
            token="token2",
            profile="worker",
            paired_at="2026-11-10T01:00:00Z",
        )
        with pytest.raises(FingerprintMismatchError):
            registry.save_peer(peer_tampered, allow_re_pair=False)

        # With explicit allow_re_pair=True, it succeeds
        registry.save_peer(peer_tampered, allow_re_pair=True)
        reloaded = registry.get_peer("node-laptop-1")
        assert reloaded.spki_sha256 == "fp_ATTACKER_2222222222222222222"


def test_signature_rejection_branches_return_401(tmp_path: Path):
    """Verify all 8 Ed25519 signature rejection branches return HTTP 401 with expected error code."""
    import base64
    import json
    import time
    from api_support import auth, build_client, make_config, mint
    from core.peer.artifacts import PeerArtifactStore
    from core.peer.crypto import ed25519_public_to_raw
    from core.peer.identity import NodeIdentity
    from core.peer.pairing import PeerRegistry, PinnedPeer
    from core.peer.store import PeerTaskStore

    store = PeerTaskStore(tmp_path / "sig_test.db")
    config = make_config(tmp_path, workspaces={"test": str(tmp_path / "ws")})
    _rec, tok = mint(config, "reader")
    time.sleep(1.05)

    ident = NodeIdentity.create_new(hostname="client-node")
    registry = PeerRegistry(tmp_path / "peers.json")
    registry.save_peer(
        PinnedPeer(
            node_id=ident.node_id,
            name="Client Node",
            endpoint="https://127.0.0.1:8443",
            spki_sha256=ident.spki_sha256,
            token="peer-token",
            profile="reader",
            paired_at="2026-01-01T00:00:00+00:00",
            static_pub_raw=base64.b64encode(ed25519_public_to_raw(ident.public_key)).decode("ascii"),
        )
    )

    client = build_client(
        config,
        peer_store=store,
        peer_registry=registry,
        peer_artifact_store=PeerArtifactStore(tmp_path / "artifacts"),
    )

    base_payload = {
        "task_id": "t-sig-1",
        "trace_id": "tr-sig-1",
        "root_task_id": "t-sig-1",
        "origin_node": ident.node_id,
        "coordinator_node": ident.node_id,
        "workspace": "test",
        "prompt": "Test signature branches",
    }
    body_bytes = json.dumps(base_payload).encode("utf-8")

    # 1. Unsigned request from pinned peer -> 401 signature_required
    r1 = client.post(
        "/tasks",
        json=base_payload,
        headers={**auth(tok), "X-Brahma-Node": ident.node_id},
    )
    assert r1.status_code == 401
    assert r1.json()["error"]["code"] == "signature_required"

    # 2. Signature present but missing timestamp or nonce -> 401 missing_signature_headers
    r2 = client.post(
        "/tasks",
        json=base_payload,
        headers={
            **auth(tok),
            "X-Brahma-Node": ident.node_id,
            "X-Brahma-Signature": "fake-sig",
        },
    )
    assert r2.status_code == 401
    assert r2.json()["error"]["code"] == "missing_signature_headers"

    # 3. Invalid timestamp -> 401 invalid_timestamp
    r3 = client.post(
        "/tasks",
        json=base_payload,
        headers={
            **auth(tok),
            "X-Brahma-Node": ident.node_id,
            "X-Brahma-Signature": "fake-sig",
            "X-Brahma-Timestamp": "not-a-number",
            "X-Brahma-Nonce": "nonce-1",
        },
    )
    assert r3.status_code == 401
    assert r3.json()["error"]["code"] == "invalid_timestamp"

    # 4. Expired timestamp (> 300s skewed) -> 401 signature_expired
    r4 = client.post(
        "/tasks",
        json=base_payload,
        headers={
            **auth(tok),
            "X-Brahma-Node": ident.node_id,
            "X-Brahma-Signature": "fake-sig",
            "X-Brahma-Timestamp": str(time.time() - 400),
            "X-Brahma-Nonce": "nonce-2",
        },
    )
    assert r4.status_code == 401
    assert r4.json()["error"]["code"] == "signature_expired"

    # 5. Unregistered peer signing -> 401 unregistered_peer
    other_ident = NodeIdentity.create_new(hostname="unknown-node")
    now_ts = str(int(time.time()))
    payload_unreg = {**base_payload, "task_id": "t-sig-unreg", "origin_node": other_ident.node_id, "coordinator_node": other_ident.node_id}
    body_unreg = json.dumps(payload_unreg).encode("utf-8")
    sig_unreg = other_ident.sign_request("POST", "/tasks", body_unreg, now_ts, "nonce-3")
    r5 = client.post(
        "/tasks",
        json=payload_unreg,
        headers={
            **auth(tok),
            "X-Brahma-Node": other_ident.node_id,
            "X-Brahma-Signature": sig_unreg,
            "X-Brahma-Timestamp": now_ts,
            "X-Brahma-Nonce": "nonce-3",
        },
    )
    assert r5.status_code == 401
    assert r5.json()["error"]["code"] == "unregistered_peer"

    # 6. Invalid / tampered signature -> 401 invalid_signature
    r6 = client.post(
        "/tasks",
        json=base_payload,
        headers={
            **auth(tok),
            "X-Brahma-Node": ident.node_id,
            "X-Brahma-Signature": base64.b64encode(b"0" * 64).decode("ascii"),
            "X-Brahma-Timestamp": now_ts,
            "X-Brahma-Nonce": "nonce-4",
        },
    )
    assert r6.status_code == 401
    assert r6.json()["error"]["code"] == "invalid_signature"

    # 7. Valid signature succeeds!
    sig_valid = ident.sign_request("POST", "/tasks", body_bytes, now_ts, "nonce-valid-1")
    r7 = client.post(
        "/tasks",
        content=body_bytes,
        headers={
            **auth(tok),
            "Content-Type": "application/json",
            "X-Brahma-Node": ident.node_id,
            "X-Brahma-Signature": sig_valid,
            "X-Brahma-Timestamp": now_ts,
            "X-Brahma-Nonce": "nonce-valid-1",
        },
    )
    assert r7.status_code == 202

    # 8. Replayed nonce -> 401 nonce_replayed
    r8 = client.post(
        "/tasks",
        content=body_bytes,
        headers={
            **auth(tok),
            "Content-Type": "application/json",
            "X-Brahma-Node": ident.node_id,
            "X-Brahma-Signature": sig_valid,
            "X-Brahma-Timestamp": now_ts,
            "X-Brahma-Nonce": "nonce-valid-1",
        },
    )
    assert r8.status_code == 401
    assert r8.json()["error"]["code"] == "nonce_replayed"

    # 9. Cancel unsigned from pinned peer -> 401 signature_required (closes mutating signature asymmetry)
    r9 = client.post(
        "/tasks/t-sig-1/cancel",
        headers={**auth(tok), "X-Brahma-Node": ident.node_id},
    )
    assert r9.status_code == 401
    assert r9.json()["error"]["code"] == "signature_required"

    # 10. Cancel signed from pinned peer -> 200
    now_cancel_ts = str(int(time.time()))
    cancel_sig = ident.sign_request("POST", "/tasks/t-sig-1/cancel", b"", now_cancel_ts, "nonce-cancel-1")
    r10 = client.post(
        "/tasks/t-sig-1/cancel",
        headers={
            **auth(tok),
            "X-Brahma-Node": ident.node_id,
            "X-Brahma-Signature": cancel_sig,
            "X-Brahma-Timestamp": now_cancel_ts,
            "X-Brahma-Nonce": "nonce-cancel-1",
        },
    )
    assert r10.status_code == 200
    assert r10.json()["task_id"] == "t-sig-1"
    assert r10.json()["cancel_requested_at"] is not None

    # 11. Artifact upload unsigned from pinned peer -> 401 signature_required (mutating action symmetry)
    artifact_bytes = b"sample artifact content"
    r11 = client.post(
        "/tasks/t-sig-1/artifacts",
        content=artifact_bytes,
        headers={
            **auth(tok),
            "Content-Type": "text/plain",
            "X-Brahma-Node": ident.node_id,
        },
    )
    assert r11.status_code == 401
    assert r11.json()["error"]["code"] == "signature_required"

    # 12. Artifact upload signed from pinned peer -> 201 Created
    now_art_ts = str(int(time.time()))
    art_sig = ident.sign_request("POST", "/tasks/t-sig-1/artifacts", artifact_bytes, now_art_ts, "nonce-art-1")
    r12 = client.post(
        "/tasks/t-sig-1/artifacts",
        content=artifact_bytes,
        headers={
            **auth(tok),
            "Content-Type": "text/plain",
            "X-Brahma-Node": ident.node_id,
            "X-Brahma-Signature": art_sig,
            "X-Brahma-Timestamp": now_art_ts,
            "X-Brahma-Nonce": "nonce-art-1",
            "X-Brahma-Artifact-Name": "report.txt",
        },
    )
    assert r12.status_code == 201
    assert r12.json()["name"] == "report.txt"

    # 13. Read status unsigned for pinned peer remains allowed (read symmetry)
    r13 = client.get(
        "/tasks/t-sig-1",
        headers={**auth(tok), "X-Brahma-Node": ident.node_id},
    )
    assert r13.status_code == 200
    assert r13.json()["task_id"] == "t-sig-1"
