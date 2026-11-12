"""Contract and integration tests for Tier 3 RemoteAgentAdapter (ADR-018, §4.12, §8.5)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import threading

import pytest

from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from agent.spec import get as get_spec, register as register_spec, unregister as unregister_spec
from core.agent_bridge.adapters.remote import RemoteAgentAdapter, validate_remote_url
from core.agent_bridge.bridge import AgentBridge
from core.agent_bridge.detectors import DetectionContext, detect_remote_agents
from core.agent_bridge.errors import ForbiddenTarget
from core.agent_bridge.models import AgentRecord
from core.agent_bridge.registry import AgentRegistry


class MockRemoteAgentHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        if self.path == "/.well-known/agent.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {
                "name": "deep-research-remote",
                "version": "2.4.1",
                "description": "Remote autonomous research agent",
            }
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        elif self.path.startswith("/tasks/task-"):
            task_id = self.path.split("/")[-1]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {"task_id": task_id, "status": "completed", "output": "Remote findings report"}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        elif self.path == "/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {"status": "healthy", "authorized": bool(auth)}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b"{}"
        data = json.loads(body.decode("utf-8")) if body else {}

        if self.path == "/tasks":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {
                "task_id": "task-9988",
                "status": "queued",
                "prompt": data.get("prompt", ""),
            }
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        elif self.path.endswith("/cancel"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {"cancelled": True, "task_id": "task-9988"}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


def _start_mock_remote_server() -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), MockRemoteAgentHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}"


def test_ssrf_and_metadata_blocking():
    # Cloud metadata endpoints must always fail
    with pytest.raises(ForbiddenTarget, match="SSRF violation"):
        validate_remote_url("http://169.254.169.254/latest/meta-data", allow_insecure=True)

    with pytest.raises(ForbiddenTarget, match="SSRF violation"):
        validate_remote_url("http://metadata.google.internal/computeMetadata/v1", allow_insecure=True)

    # Insecure HTTP without allow_insecure must fail
    with pytest.raises(ForbiddenTarget, match="Insecure HTTP forbidden"):
        validate_remote_url("http://api.external-agent.org/v1", allow_insecure=False)

    # Loopback or private IPs without allow_insecure must fail
    with pytest.raises(ForbiddenTarget, match="Private/loopback IP"):
        validate_remote_url("https://127.0.0.1:8443/agent", allow_insecure=False)

    with pytest.raises(ForbiddenTarget, match="Private/loopback IP"):
        validate_remote_url("https://192.168.1.50:8443/agent", allow_insecure=False)

    # Valid HTTPS public URL succeeds
    validate_remote_url("https://agent.example.com/api", allow_insecure=False)


def test_detect_remote_agents_via_env(tmp_path: Path):
    cfg = [
        {
            "id": "remote_cloud_coder",
            "display_name": "Cloud Coding Agent",
            "endpoint": "https://coder.example.com/api",
            "tier3": {"allow_insecure": False, "verify_ssl": True},
        }
    ]
    ctx = DetectionContext(
        which_fn=lambda _: None,
        port_check_fn=lambda _h, _p, _t=0.25: False,
        env={"BRAHMA_REMOTE_AGENTS": json.dumps(cfg)},
        home_dir=tmp_path,
    )
    records = detect_remote_agents(ctx)
    assert len(records) == 1
    assert records[0].id == "remote_cloud_coder"
    assert records[0].tier == 3
    assert records[0].kind == "remote_http"
    assert records[0].endpoint == "https://coder.example.com/api"


def test_detect_remote_agents_via_config_file(tmp_path: Path):
    brahma_dir = tmp_path / ".brahma"
    brahma_dir.mkdir()
    cfg_file = brahma_dir / "remote_agents.json"
    cfg = [
        {
            "id": "remote_cluster",
            "display_name": "Cluster Worker",
            "endpoint": "https://cluster.example.org/a2a",
        }
    ]
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")

    ctx = DetectionContext(
        which_fn=lambda _: None,
        port_check_fn=lambda _h, _p, _t=0.25: False,
        env={},
        home_dir=tmp_path,
    )
    records = detect_remote_agents(ctx)
    assert len(records) == 1
    assert records[0].id == "remote_cluster"
    assert "remote_config" in records[0].sources


def test_remote_agent_probe_success():
    server, endpoint = _start_mock_remote_server()
    try:
        record = AgentRecord(
            id="test_remote",
            display_name="Test Remote Agent",
            kind="remote_http",
            tier=3,
            endpoint=endpoint,
            tier3={"allow_insecure": True, "verify_ssl": False},
        )
        adapter = RemoteAgentAdapter(record)
        probe = adapter.probe()

        assert probe.ok is True
        assert probe.stage == "capability"
        assert probe.version == "2.4.1"
    finally:
        server.shutdown()


def test_remote_agent_probe_rejection_on_ssrf():
    record = AgentRecord(
        id="bad_remote",
        display_name="Malicious Cloud Metadata",
        kind="remote_http",
        tier=3,
        endpoint="http://169.254.169.254/latest",
        tier3={"allow_insecure": True},
    )
    adapter = RemoteAgentAdapter(record)
    probe = adapter.probe()
    assert probe.ok is False
    assert "SSRF violation" in probe.detail


def test_remote_agent_verbs_and_a2a_calls(monkeypatch):
    server, endpoint = _start_mock_remote_server()
    monkeypatch.setenv("BRAHMA_AGENT_TOKEN_REMOTE_SECURE", "secret-test-token-xyz")
    try:
        record = AgentRecord(
            id="remote_secure",
            display_name="Secure Remote Agent",
            kind="remote_http",
            tier=3,
            endpoint=endpoint,
            tier3={"allow_insecure": True, "verify_ssl": False},
        )
        adapter = RemoteAgentAdapter(record)
        adapter.connect()

        verbs = adapter.verbs()
        verb_names = {v.name for v in verbs}
        assert "run_task" in verb_names
        assert "query_status" in verb_names
        assert "cancel_task" in verb_names

        # Call run_task
        res_run = adapter.call("run_task", {"prompt": "Analyze dataset", "context": {"rows": 500}})
        assert res_run.ok is True
        assert "task-9988" in res_run.output
        assert isinstance(res_run.data, dict)

        # Call query_status
        res_stat = adapter.call("query_status", {"task_id": "task-9988"})
        assert res_stat.ok is True
        assert "Remote findings report" in res_stat.output

        # Call cancel_task
        res_cancel = adapter.call("cancel_task", {"task_id": "task-9988"})
        assert res_cancel.ok is True
        assert "cancelled" in res_cancel.output
    finally:
        server.shutdown()


def test_remote_agent_to_tool_specs_and_runtime_execute(tmp_path: Path):
    server, endpoint = _start_mock_remote_server()
    try:
        record = AgentRecord(
            id="tier3_remote",
            display_name="Tier 3 Remote Worker",
            kind="remote_http",
            tier=3,
            endpoint=endpoint,
            tier3={"allow_insecure": True, "verify_ssl": False},
        )
        adapter = RemoteAgentAdapter(record)
        specs = adapter.to_tool_specs()
        assert len(specs) == 4

        spec_map = {s.name: s for s in specs}
        assert "agent_tier3_remote_run_task" in spec_map
        assert "agent_tier3_remote_query_status" in spec_map
        assert "agent_tier3_remote_fetch_artifact" in spec_map

        run_spec = spec_map["agent_tier3_remote_run_task"]
        query_spec = spec_map["agent_tier3_remote_query_status"]

        # Tool class should be network
        assert run_spec.tool_class == "network"
        assert query_spec.tool_class == "network"

        # Mutating verbs require approval
        assert run_spec.requires_approval is True
        assert query_spec.requires_approval is False

        # Register and execute through AgentRuntime
        for s in specs:
            register_spec(s)
        try:
            runtime = AgentRuntime(
                workspace=tmp_path,
                policy=PermissionPolicy.from_dict({
                    "classes": {"network": "allow"},
                    "headless": {"network": "allow"},
                }),
            )
            out = runtime.execute("agent_tier3_remote_query_status", {"task_id": "task-7711"})
            assert "Remote findings report" in out
        finally:
            for s in specs:
                unregister_spec(s.name)
    finally:
        server.shutdown()


def test_bridge_discover_and_publish_remote_agent(tmp_path: Path, monkeypatch):
    reg_file = tmp_path / "registry.json"
    registry = AgentRegistry(store_path=reg_file)
    bridge = AgentBridge(registry=registry, artifacts_dir=tmp_path / "artifacts")

    server, endpoint = _start_mock_remote_server()
    try:
        fake_rec = AgentRecord(
            id="remote_service",
            display_name="Remote Service",
            kind="remote_http",
            tier=3,
            endpoint=endpoint,
            tier3={"allow_insecure": True},
        )
        monkeypatch.setattr("core.agent_bridge.bridge.run_all", lambda _ctx: [fake_rec])

        discovered = bridge.discover(workspace=tmp_path)
        assert len(discovered) == 1
        assert "remote_service" in bridge._adapters

        published = bridge.publish_tools()
        assert "agent_remote_service_run_task" in published
        assert "agent_remote_service_query_status" in published

        spec = get_spec("agent_remote_service_run_task")
        assert spec is not None
        assert spec.tool_class == "network"

        bridge.close()
        assert get_spec("agent_remote_service_run_task") is None
    finally:
        server.shutdown()


def test_remote_adapter_auto_signs_when_paired_or_pinned():
    """Verify RemoteAgentAdapter automatically signs mutating requests when pinned SPKI or node_identity is present."""
    from core.peer.identity import NodeIdentity

    ident = NodeIdentity.create_new(hostname="test-sender")
    received_headers = {}

    class HeaderCaptureHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            nonlocal received_headers
            received_headers = dict(self.headers)
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"task_id": "t-signed-1", "state": "submitted"}')

    server = HTTPServer(("127.0.0.1", 0), HeaderCaptureHandler)
    port = server.server_port
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()

    endpoint = f"http://127.0.0.1:{port}"
    try:
        record = AgentRecord(
            id="paired_peer",
            display_name="Paired Peer",
            kind="remote_http",
            tier=3,
            endpoint=endpoint,
            tier3={
                "allow_insecure": True,
                "pinned_spki": "aa" * 32,
                "node_identity": ident,
            },
        )
        adapter = RemoteAgentAdapter(record)
        assert adapter._sign_requests is True

        res = adapter.call("run_task", {"prompt": "Run paired task", "workspace": "test"})
        assert res.ok is True
        assert "X-Brahma-Node" in received_headers
        assert received_headers["X-Brahma-Node"] == ident.node_id
        assert "X-Brahma-Signature" in received_headers
        assert "X-Brahma-Timestamp" in received_headers
        assert "X-Brahma-Nonce" in received_headers
    finally:
        server.shutdown()


def test_remote_adapter_endpoint_scan_auto_detects_registry_peer(tmp_path: Path, monkeypatch):
    """Verify endpoint scan against PeerRegistry automatically enables request signing and pinned SPKI."""
    from core.peer.pairing import PeerRegistry, PinnedPeer

    # Setup peer in custom registry file
    reg_file = tmp_path / "peer" / "peers.json"
    registry = PeerRegistry(reg_file)
    peer = PinnedPeer(
        node_id="peer-scan-1",
        name="Auto Detected Peer",
        endpoint="http://127.0.0.1:9876",
        spki_sha256="expected-registry-spki-sha256",
        token="peer-token-abc",
        profile="worker",
        paired_at="2026-01-01T00:00:00+00:00",
        static_pub_raw="raw-pub-bytes",
    )
    registry.save_peer(peer)
    assert len(registry.list_peers()) == 1

    # 1. Verification via explicit tier3["registry_path"]
    record_custom = AgentRecord(
        id="auto_peer_custom",
        display_name="Auto Peer Custom",
        kind="remote_http",
        tier=3,
        endpoint="http://127.0.0.1:9876",
        tier3={"registry_path": reg_file},
    )
    adapter_custom = RemoteAgentAdapter(record_custom)
    assert adapter_custom._sign_requests is True
    assert adapter_custom._pinned_spki == "expected-registry-spki-sha256"

    # 2. Verification via default PeerRegistry() location
    monkeypatch.setattr("core.peer.pairing.get_user_data_dir", lambda: tmp_path)
    record_default = AgentRecord(
        id="auto_peer_default",
        display_name="Auto Peer Default",
        kind="remote_http",
        tier=3,
        endpoint="http://127.0.0.1:9876",
        tier3={},
    )
    adapter_default = RemoteAgentAdapter(record_default)
    assert adapter_default._sign_requests is True
    assert adapter_default._pinned_spki == "expected-registry-spki-sha256"

    # 3. registry_path as a STRING — what persisted/hand-written config produces
    record_str = AgentRecord(
        id="auto_peer_str",
        display_name="Auto Peer Str",
        kind="remote_http",
        tier=3,
        endpoint="http://127.0.0.1:9876",
        tier3={"registry_path": str(reg_file)},
    )
    adapter_str = RemoteAgentAdapter(record_str)
    assert adapter_str._sign_requests is True
    assert adapter_str._pinned_spki == "expected-registry-spki-sha256"

    # 4. Full JSON round-trip like detectors.py loads records from disk
    record_rt = AgentRecord.from_dict(json.loads(json.dumps(record_str.to_dict())))
    adapter_rt = RemoteAgentAdapter(record_rt)
    assert adapter_rt._sign_requests is True
    assert adapter_rt._pinned_spki == "expected-registry-spki-sha256"


def test_remote_agent_adapter_auto_selects_candidate_peer_from_active_service(tmp_path: Path):
    """Verify RemoteAgentAdapter queries PeerService when endpoint is not configured."""
    from core.peer.artifacts import PeerArtifactStore
    from core.peer.pairing import PeerRegistry, PinnedPeer
    from core.peer.service import PeerService, set_active_peer_service
    from core.peer.store import PeerTaskStore

    reg = PeerRegistry(tmp_path / "peers_select.json")
    pinned = PinnedPeer(
        node_id="peer-selected-1",
        name="Dynamic Peer Worker",
        endpoint="https://192.168.1.99:8443",
        spki_sha256="select-sha-256",
        token="tok-select-99",
        profile="worker",
        paired_at="2026-11-10T00:00:00Z",
        static_pub_raw="c3RhdGlj",
        workspaces=["repo-cuda"],
        labels={"gpu": "h100"},
    )
    reg.save_peer(pinned)

    service = PeerService(
        registry=reg,
        task_store=PeerTaskStore(tmp_path / "tasks_select.db"),
        artifact_store=PeerArtifactStore(tmp_path / "artifacts_select"),
        endpoint="https://127.0.0.1:8443",
    )
    set_active_peer_service(service)

    try:
        record = AgentRecord(
            id="dynamic_remote_agent",
            display_name="Dynamic Remote Agent",
            kind="remote_http",
            tier=3,
            endpoint="",
            tier3={"profile": "worker", "workspace": "repo-cuda", "labels": {"gpu": "h100"}},
        )
        adapter = RemoteAgentAdapter(record)
        assert adapter._endpoint == "https://192.168.1.99:8443"
        assert adapter._pinned_spki == "select-sha-256"
        assert adapter._sign_requests is True
        headers = adapter._get_headers()
        assert headers["Authorization"] == "Bearer tok-select-99"
    finally:
        set_active_peer_service(None)
