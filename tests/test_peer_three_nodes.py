"""In-process 3-node Brahma Swarm / Federation simulation.

Simulates Node A (coordinator), Node B (worker) and Node C (worker), and
verifies:
1. A<->B SAS pairing, static-key pinning and token unwrap.
2. Dispatch to B (paired) and to C (a locally minted token) as separate
   endpoints.
3. Recursion rejection on B (Invariant S-08).

Concurrency (S-03) and signed idempotent retry (S-04) are covered in
tests/test_peer_server.py. A real-socket three-node run is the Stage 3
gate and needs three machines; the single-machine stand-in runs three
uvicorn servers on loopback.
"""

from __future__ import annotations

from pathlib import Path

from api_support import CaptureCompleter, auth, build_client, make_config, mint
from core.peer.identity import NodeIdentity
from core.peer.model import BrahmaTaskRequest
from core.peer.pairing import (
    PairingCoordinator,
    PeerRegistry,
    PinnedPeer,
)
from core.peer.state import WireState
from core.peer.store import PeerTaskStore


def test_three_node_swarm_pairing_and_federated_execution(tmp_path: Path):
    """Simulate 3 nodes: pairing, SPKI pinning, and federated task dispatch."""
    # 1. Setup Identities for 3 nodes
    node_a = NodeIdentity.create_new(hostname="node-a-laptop")
    node_b = NodeIdentity.create_new(hostname="node-b-workshop")
    node_c = NodeIdentity.create_new(hostname="node-c-server")

    assert len({node_a.node_id, node_b.node_id, node_c.node_id}) == 3

    # 2. Simulate SAS pairing between Node A (Coordinator) and Node B (Worker)
    coord_a = PairingCoordinator(node_a, registry=PeerRegistry(tmp_path / "peers_a.json"))
    coord_b = PairingCoordinator(node_b, registry=PeerRegistry(tmp_path / "peers_b.json"))

    offer_b = coord_b.create_offer(host="192.168.1.20", port=8000)

    a_eph_priv, a_eph_pub, a_static_pub = coord_a.begin_exchange(offer_b, is_initiator=True)
    b_eph_priv, b_eph_pub, b_static_pub = coord_b.begin_exchange(offer_b, is_initiator=False)

    sas_a, session_key_a = coord_a.complete_key_exchange(
        eph_priv=a_eph_priv,
        peer_node_id=node_b.node_id,
        peer_eph_pub_bytes=b_eph_pub,
        peer_static_pub_bytes=b_static_pub,
        peer_spki_sha256=node_b.spki_sha256,
        nonce=offer_b.nonce,
        is_initiator=True,
    )
    sas_b, session_key_b = coord_b.complete_key_exchange(
        eph_priv=b_eph_priv,
        peer_node_id=node_a.node_id,
        peer_eph_pub_bytes=a_eph_pub,
        peer_static_pub_bytes=a_static_pub,
        peer_spki_sha256=node_a.spki_sha256,
        nonce=offer_b.nonce,
        is_initiator=False,
    )
    # Verification of SAS code equality
    assert sas_a == sas_b

    # Callee B wraps token for Initiator A
    enc_token = coord_b.wrap_token_payload(
        session_key=session_key_b,
        token="tok_b_for_a_secret",
        profile="worker",
        callee_node_id=node_b.node_id,
        callee_spki_sha256=node_b.spki_sha256,
        callee_endpoint="https://192.168.1.20:8000",
    )
    unwrapped_b = coord_a.unwrap_token_payload(session_key_a, enc_token)
    assert unwrapped_b["token"] == "tok_b_for_a_secret"

    # Store pinned peer on A
    coord_a.registry.save_peer(PinnedPeer(
        node_id=node_b.node_id,
        name="Workshop PC",
        endpoint="https://192.168.1.20:8000",
        spki_sha256=node_b.spki_sha256,
        token=unwrapped_b["token"],
        profile="worker",
        paired_at="2026-11-10T00:00:00Z",
    ))

    # 3. Spin up Node B's FastAPI peer endpoint
    b_store = PeerTaskStore(tmp_path / "tasks_b.db")
    b_cfg = make_config(
        tmp_path / "b_cfg",
        node_name="Workshop PC",
        workspaces={"repo_b": str(tmp_path / "repo_b")},
    )
    b_cfg.host = node_b.node_id
    _rec, b_token = mint(b_cfg, "worker", confirm="worker")
    b_completer = CaptureCompleter("Results from Node B analysis.")
    b_client = build_client(
        b_cfg,
        factory=lambda *_args, **_kwargs: b_completer,
        peer_store=b_store,
        peer_identity=node_b,
    )

    # 4. Spin up Node C's FastAPI peer endpoint
    c_store = PeerTaskStore(tmp_path / "tasks_c.db")
    c_cfg = make_config(
        tmp_path / "c_cfg",
        node_name="Server",
        workspaces={"repo_c": str(tmp_path / "repo_c")},
    )
    c_cfg.host = node_c.node_id
    _rec, c_token = mint(c_cfg, "reader")
    c_completer = CaptureCompleter("Results from Node C scan.")
    c_client = build_client(
        c_cfg,
        factory=lambda *_args, **_kwargs: c_completer,
        peer_store=c_store,
        peer_identity=node_c,
    )

    # 5. Coordinator A dispatches synchronous task to Node B
    task_b = BrahmaTaskRequest(
        task_id="task-b-1",
        trace_id="tr-1",
        root_task_id="task-b-1",
        origin_node=node_a.node_id,
        coordinator_node=node_a.node_id,
        workspace="repo_b",
        prompt="Inspect repo_b structure",
        profile="worker",
        reply={"mode": "sync"},
    )
    res_b = b_client.post(
        "/tasks",
        headers={**auth(b_token), "X-Brahma-Node": node_a.node_id},
        json=task_b.to_dict(),
    )
    assert res_b.status_code == 200
    res_b_data = res_b.json()
    assert res_b_data["state"] == WireState.COMPLETED.value
    assert "Results from Node B" in res_b_data["result"]["text"]

    # 6. Coordinator A dispatches task to Node C
    task_c = BrahmaTaskRequest(
        task_id="task-c-1",
        trace_id="tr-2",
        root_task_id="task-c-1",
        origin_node=node_a.node_id,
        coordinator_node=node_a.node_id,
        workspace="repo_c",
        prompt="Scan repo_c",
        profile="reader",
        reply={"mode": "sync"},
    )
    res_c = c_client.post(
        "/tasks",
        headers={**auth(c_token), "X-Brahma-Node": node_a.node_id},
        json=task_c.to_dict(),
    )
    assert res_c.status_code == 200
    res_c_data = res_c.json()
    assert res_c_data["state"] == WireState.COMPLETED.value
    assert "Results from Node C" in res_c_data["result"]["text"]

    # 7. Invariant S-08: Node B attempting to call itself as origin is rejected
    task_cycle = BrahmaTaskRequest(
        task_id="task-cycle",
        trace_id="tr-cycle",
        root_task_id="task-cycle",
        origin_node=node_b.node_id,  # Origin is callee itself!
        coordinator_node=node_a.node_id,
        workspace="repo_b",
        prompt="Infinite cycle prompt",
        profile="worker",
    )
    cycle_res = b_client.post(
        "/tasks",
        headers={**auth(b_token), "X-Brahma-Node": node_b.node_id},
        json=task_cycle.to_dict(),
    )
    assert cycle_res.status_code == 400
    assert cycle_res.json()["error"]["code"] == "recursion_denied"
    assert "task originated from self" in cycle_res.json()["error"]["message"]
