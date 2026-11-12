"""Tests for the federation tool surface (``agent/peer_tools.py``).

The tools are the model's only window into the peer federation, so these cover
the three things that can go wrong silently: a token leaking into a tool
result, a dispatch to a peer this node cannot reach, and a tool that claims a
task ran when the peer never answered.
"""

from __future__ import annotations

import json
import socket
import ssl
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest
import uvicorn

from agent.peer_tools import dispatch_peer_task, list_peers, peer_status
from agent.prompts import federation_prompt_block
from agent.spec import ToolContext, get as get_spec
from brahma_api.auth import TokenRegistry
from brahma_api.runner import AgentRunner
from brahma_api.server import create_app
from core.peer.artifacts import PeerArtifactStore
from core.peer.identity import NodeIdentity
from core.peer.pairing import PairingCoordinator, PeerRegistry, PinnedPeer
from core.peer.service import PeerService, set_active_peer_service
from core.peer.store import PeerTaskStore
from tests.api_support import CaptureCompleter, make_config


@pytest.fixture(autouse=True)
def _clear_active_service(monkeypatch, tmp_path: Path):
    """No test may inherit a live service or write to the real user data dir.

    ``PeerRegistry()`` with no argument resolves ``%LOCALAPPDATA%\\BrahmaAI``;
    a test that reaches that default would pin a fake peer into the operator's
    real trust store. Redirect the default before any test body runs. Both
    bindings are patched because ``pairing`` holds a module-level import while
    ``agent.prompts`` imports ``get_user_data_dir`` at call time.
    """
    import core.peer.pairing as pairing
    import core.user_paths as user_paths

    user_dir = tmp_path / "userdata"
    user_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(pairing, "get_user_data_dir", lambda: user_dir)
    monkeypatch.setattr(user_paths, "get_user_data_dir", lambda: user_dir)
    set_active_peer_service(None)
    yield
    set_active_peer_service(None)


def _service(tmp_path: Path, *, name: str = "tool-host") -> PeerService:
    ident = NodeIdentity.create_new(machine_guid=f"guid-{name}", hostname=name)
    service = PeerService(
        identity=ident,
        registry=PeerRegistry(tmp_path / "peers.json"),
        task_store=PeerTaskStore(tmp_path / "tasks.db"),
        artifact_store=PeerArtifactStore(tmp_path / "artifacts"),
        endpoint="https://127.0.0.1:8443",
    )
    set_active_peer_service(service)
    return service


def _context(tmp_path: Path, service: Any = None) -> ToolContext:
    extra: Dict[str, Any] = {}
    if service is not None:
        extra["peer_service"] = service
    return ToolContext(workspace=tmp_path, extra=extra)


def _pin(service: PeerService, **overrides: Any) -> PinnedPeer:
    values = dict(
        node_id="node-alpha",
        name="Alpha Node",
        endpoint="https://10.0.0.10:8443",
        spki_sha256="cc" * 32,
        token="tok-alpha-secret",
        profile="worker",
        paired_at="2026-01-01T00:00:00Z",
        workspaces=["repo"],
    )
    values.update(overrides)
    peer = PinnedPeer(**values)
    service.registry.save_peer(peer)
    return peer


def test_peer_specs_are_classified_for_policy():
    assert get_spec("list_peers").tool_class == "read"
    assert get_spec("peer_status").tool_class == "network"
    assert get_spec("dispatch_peer_task").tool_class == "exec"


def test_list_peers_reports_local_and_pinned_without_secrets(tmp_path: Path):
    service = _service(tmp_path)
    _pin(service)

    raw = list_peers({}, _context(tmp_path, service))
    payload = json.loads(raw)

    assert payload["local"]["node_id"] == service.identity.node_id
    assert payload["local"]["available"] is True
    assert payload["pinned_peers"][0]["node_id"] == "node-alpha"
    assert payload["pinned_peers"][0]["outbound_token"] is True
    assert payload["pinned_peers"][0]["workspaces"] == ["repo"]
    # The registry stores the bearer token; it must never reach the model.
    assert "tok-alpha-secret" not in raw
    assert "static_pub_raw" not in raw


def test_list_peers_without_service_is_explicit(tmp_path: Path):
    empty = PeerRegistry(tmp_path / "empty_peers.json")
    raw = list_peers({}, ToolContext(workspace=tmp_path, extra={"peer_registry": empty}))
    assert "No peer service is running" in raw


def test_peer_status_rejects_an_ambiguous_selector(tmp_path: Path):
    service = _service(tmp_path)
    _pin(service, node_id="node-alpha", name="Alpha")
    _pin(service, node_id="node-beta", name="Beta")

    out = peer_status({"peer": "node-"}, _context(tmp_path, service))
    assert "does not identify one pinned peer" in out
    assert "node-alpha" in out and "node-beta" in out


def test_dispatch_refuses_a_peer_with_no_outbound_token(tmp_path: Path):
    service = _service(tmp_path)
    _pin(service, token="")

    out = dispatch_peer_task(
        {"peer": "Alpha Node", "prompt": "say hi"}, _context(tmp_path, service)
    )
    assert "no outbound token" in out
    assert "directional" in out


def test_federation_prompt_block_is_conditional(monkeypatch):
    from agent import prompts

    monkeypatch.setattr(prompts, "_has_peer_federation", lambda: False)
    assert federation_prompt_block() == ""

    monkeypatch.setattr(prompts, "_has_peer_federation", lambda: True)
    text = prompts.federation_prompt_block(dispatch_available=False)
    assert "list_peers" in text
    assert "dispatch_peer_task is not on this surface" in text
    assert "untrusted input" in text


def test_build_system_prompt_adds_federation_only_with_a_pinned_peer(tmp_path: Path):
    """No pin, no service: the block costs nothing. One pin: it appears.

    This goes through the real ``PeerRegistry()`` default (redirected to a temp
    dir by the autouse fixture) rather than patching the predicate, so the
    disk-backed detection path is what is exercised.
    """
    from agent.prompts import build_system_prompt

    clean = build_system_prompt(tmp_path, include_brain=False, include_subagents=False)
    assert "Peer federation" not in clean

    from core.peer.pairing import PeerRegistry

    PeerRegistry().save_peer(
        PinnedPeer(
            node_id="node-pinned",
            name="Pinned Node",
            endpoint="https://10.0.0.11:8443",
            spki_sha256="ee" * 32,
            token="tok-pinned",
            profile="worker",
            paired_at="2026-01-01T00:00:00Z",
        )
    )
    seeded = build_system_prompt(tmp_path, include_brain=False, include_subagents=False)
    assert "Peer federation" in seeded
    assert "list_peers" in seeded
    assert "dispatch_peer_task" in seeded
    # The resident prompt must not leak the bearer token from the registry.
    assert "tok-pinned" not in seeded

    # A child loop skips the block: its surface has the read/network pair but
    # not dispatch, so the block would promise a tool it was not given.
    child = build_system_prompt(
        tmp_path, include_brain=False, include_subagents=False, include_federation=False
    )
    assert "Peer federation" not in child


# --------------------------------------------------------------------------- #
# End-to-end: a real TLS peer, a real SAS pairing, a real signed dispatch
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_server(app: Any, port: int, cert: Path, key: Path) -> Tuple[uvicorn.Server, threading.Thread]:
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
            lifespan="off",
            ssl_certfile=str(cert),
            ssl_keyfile=str(key),
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15.0
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise AssertionError("peer test server did not start")
    return server, thread


def _pair_over_http(
    initiator: NodeIdentity,
    callee: NodeIdentity,
    service: PeerService,
    endpoint: str,
) -> str:
    """Run the callee's SAS exchange and return the token it minted for us."""
    offer = service.coordinator.create_offer(host="127.0.0.1", port=8443)
    service.pending_offers[offer.nonce] = offer

    coord = PairingCoordinator(initiator, PeerRegistry())
    eph_priv, eph_pub, static_pub = coord.begin_exchange(offer, is_initiator=True)
    import base64

    payload = {
        "offer_uri": offer.to_uri(),
        "initiator_node_id": initiator.node_id,
        "initiator_spki_sha256": initiator.spki_sha256,
        "initiator_name": initiator.hostname,
        "initiator_endpoint": "https://127.0.0.1:9",
        "initiator_eph_pub": base64.b64encode(eph_pub).decode("ascii"),
        "initiator_static_pub": base64.b64encode(static_pub).decode("ascii"),
        "profile": "worker",
        "workspaces": ["repo"],
    }
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(
        f"{endpoint}/pair/exchange",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, context=ctx, timeout=10.0) as response:
        exchanged = json.loads(response.read().decode("utf-8"))

    sas, session_key = coord.complete_key_exchange(
        eph_priv=eph_priv,
        peer_node_id=exchanged["callee_node_id"],
        peer_eph_pub_bytes=base64.b64decode(exchanged["callee_eph_pub"]),
        peer_static_pub_bytes=base64.b64decode(exchanged["callee_static_pub"]),
        peer_spki_sha256=exchanged["callee_spki_sha256"],
        nonce=offer.nonce,
        is_initiator=True,
    )
    assert sas == exchanged["sas_code"]
    unwrapped = coord.unwrap_token_payload(
        session_key, base64.b64decode(exchanged["encrypted_token"])
    )
    return str(unwrapped["token"])


def test_dispatch_peer_task_end_to_end_over_tls(tmp_path: Path):
    """Pair A->B over HTTP, then dispatch with the tool through the real adapter."""
    node_a = NodeIdentity.create_new(machine_guid="guid-node-a", hostname="node-a")
    node_b = NodeIdentity.create_new(machine_guid="guid-node-b", hostname="node-b")

    port = _free_port()
    endpoint = f"https://127.0.0.1:{port}"

    b_config = make_config(
        tmp_path / "b_cfg", workspaces={"repo": str(tmp_path / "repo_b")}
    )
    b_registry = PeerRegistry(tmp_path / "b_peers.json")
    b_service = PeerService(
        identity=node_b,
        registry=b_registry,
        task_store=PeerTaskStore(tmp_path / "b_tasks.db"),
        artifact_store=PeerArtifactStore(tmp_path / "b_artifacts"),
        endpoint=endpoint,
    )
    completer = CaptureCompleter("Results from Node B analysis.")
    app = create_app(
        b_config,
        registry=TokenRegistry(b_config.tokens_path),
        runner=AgentRunner(b_config, completer_factory=lambda *a, **k: completer),
        peer_identity=node_b,
        peer_registry=b_registry,
        peer_store=b_service.store,
        peer_artifact_store=b_service.artifact_store,
        peer_service=b_service,
    )

    cert, key = node_b.ensure_certificate_files(cert_dir=tmp_path / "b_identity")
    server, thread = _start_server(app, port, cert, key)
    try:
        token = _pair_over_http(node_a, node_b, b_service, endpoint)

        # The callee records the initiator in *its* registry, with no token.
        # If this lands in the default registry instead, a test would be
        # pinning a fake node into the operator's real trust store.
        pinned_initiator = b_registry.get_peer(node_a.node_id)
        assert pinned_initiator is not None
        assert pinned_initiator.token == ""

        # A is the initiator, so A is the side that holds the outbound token.
        a_service = PeerService(
            identity=node_a,
            registry=PeerRegistry(tmp_path / "a_peers.json"),
            task_store=PeerTaskStore(tmp_path / "a_tasks.db"),
            artifact_store=PeerArtifactStore(tmp_path / "a_artifacts"),
            endpoint="https://127.0.0.1:9",
        )
        a_service.registry.save_peer(
            PinnedPeer(
                node_id=node_b.node_id,
                name="Workshop PC",
                endpoint=endpoint,
                spki_sha256=node_b.spki_sha256,
                token=token,
                profile="worker",
                paired_at="2026-01-01T00:00:00Z",
                workspaces=["repo"],
            )
        )

        out = dispatch_peer_task(
            {"peer": "Workshop PC", "prompt": "Inspect the repo", "deadline_s": 60},
            _context(tmp_path, a_service),
        )
        payload = json.loads(out)

        assert payload["peer"] == node_b.node_id
        assert payload["task_id"], out
        assert payload["state"] == "completed", out
        assert "Results from Node B" in payload["result"]
        assert completer.calls, "the peer never ran its own completer"
        # The token the pairing minted must never appear in the tool result.
        assert token not in out

        # peer_status: node summary and one task, both through the same signed
        # adapter. This also proves the pairing token carries peer:status.
        node_out = json.loads(peer_status({"peer": "Workshop PC"}, _context(tmp_path, a_service)))
        assert node_out["peer"] == node_b.node_id
        assert node_out["drain_mode"] is False
        assert node_out["name"] == "node-b"

        task_out = json.loads(
            peer_status(
                {"peer": "Workshop PC", "task_id": payload["task_id"]},
                _context(tmp_path, a_service),
            )
        )
        assert task_out["state"] == "completed", task_out
        assert "Results from Node B" in task_out["result"]
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
