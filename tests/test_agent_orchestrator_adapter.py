"""Contract and integration tests for Tier 2 Orchestrator Adapter (ADR-018)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys
import threading

import pytest

from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from agent.spec import get as get_spec, register as register_spec, unregister as unregister_spec
from core.agent_bridge.adapters.orchestrator import OrchestratorAdapter
from core.agent_bridge.bridge import AgentBridge
from core.agent_bridge.detectors import DetectionContext, detect_orchestrators
from core.agent_bridge.errors import ForbiddenTarget
from core.agent_bridge.models import AgentRecord, AgentTransport
from core.agent_bridge.registry import AgentRegistry


class MockPaperclipHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Quiet test logging

    def do_GET(self):
        if self.path == "/api/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "healthy", "fleet": 4}')
        elif self.path == "/api/agents":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = [{"id": "agent-1", "name": "coder-alpha", "state": "idle"}]
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        elif self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {"fleet_status": "operational", "active_issues": 1}
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b"{}"
        data = json.loads(body.decode("utf-8")) if body else {}

        if self.path == "/api/issues":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {"id": "issue-99", "title": data.get("title", ""), "status": "dispatched"}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        elif self.path == "/api/heartbeat":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {"heartbeat": "acknowledged", "pulse_id": "pulse-123"}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


def _start_mock_paperclip() -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), MockPaperclipHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}/api"


def _make_mock_orca_script(tmp_path: Path) -> Path:
    script = tmp_path / "mock_orca.py"
    script.write_text(
        r'''
import sys
import json

args = sys.argv[1:]
if not args or args[0] == "--version":
    print("orca-ade version 0.9.1")
    sys.exit(0)

if args[0] == "worktrees" and "--json" in args:
    print(json.dumps([{"id": "wt-core", "path": "D:/work/core"}]))
    sys.exit(0)

if args[0] == "assign":
    goal = ""
    worktree = ""
    for i, a in enumerate(args):
        if a == "--goal" and i + 1 < len(args):
            goal = args[i + 1]
        elif a == "--worktree" and i + 1 < len(args):
            worktree = args[i + 1]
    print(f"Goal '{goal}' assigned to {worktree or 'default'}")
    sys.exit(0)

print(f"Unknown command: {args}", file=sys.stderr)
sys.exit(1)
''',
        encoding="utf-8",
    )
    return script


def test_detect_paperclip_via_env(tmp_path):
    ctx = DetectionContext(
        which_fn=lambda _: None,
        port_check_fn=lambda _h, _p, _t=0.25: False,
        env={"PAPERCLIP_API_URL": "http://127.0.0.1:3100/custom/api"},
        home_dir=tmp_path,
    )
    records = detect_orchestrators(ctx)
    assert len(records) == 1
    assert records[0].id == "orchestrator_paperclip"
    assert records[0].kind == "orchestrator"
    assert records[0].tier == 2
    assert records[0].endpoint == "http://127.0.0.1:3100/custom/api"


def test_detect_paperclip_via_home_dir(tmp_path):
    paperclip_dir = tmp_path / ".paperclip"
    paperclip_dir.mkdir()
    ctx = DetectionContext(
        which_fn=lambda _: None,
        port_check_fn=lambda _h, _p, _t=0.25: False,
        env={},
        home_dir=tmp_path,
    )
    records = detect_orchestrators(ctx)
    assert len(records) == 1
    assert records[0].id == "orchestrator_paperclip"
    assert "user_home" in records[0].sources


def test_detect_paperclip_via_port(tmp_path):
    ctx = DetectionContext(
        which_fn=lambda _: None,
        port_check_fn=lambda _h, port, _t=0.25: port == 3100,
        env={},
        home_dir=tmp_path,
    )
    records = detect_orchestrators(ctx)
    assert len(records) == 1
    assert records[0].id == "orchestrator_paperclip"
    assert "loopback_probe" in records[0].sources


def test_detect_orca_via_env(tmp_path):
    ctx = DetectionContext(
        which_fn=lambda _: None,
        port_check_fn=lambda _h, _p, _t=0.25: False,
        env={"ORCA_CLI_COMMAND": "C:\\Tools\\orca.exe"},
        home_dir=tmp_path,
    )
    records = detect_orchestrators(ctx)
    assert len(records) == 1
    assert records[0].id == "orchestrator_orca"
    assert records[0].executable == "C:\\Tools\\orca.exe"


def test_detect_multiple_orchestrators(tmp_path):
    paperclip_dir = tmp_path / ".paperclip"
    paperclip_dir.mkdir()
    ctx = DetectionContext(
        which_fn=lambda b: "C:\\bin\\orca.exe" if "orca" in b else None,
        port_check_fn=lambda _h, _p, _t=0.25: False,
        env={},
        home_dir=tmp_path,
    )
    records = detect_orchestrators(ctx)
    ids = {r.id for r in records}
    assert "orchestrator_paperclip" in ids
    assert "orchestrator_orca" in ids


def test_self_target_recursion_rejection(monkeypatch):
    monkeypatch.setenv("BRAHMA_GATEWAY_PORT", "8000")
    record = AgentRecord(
        id="bad_paperclip",
        display_name="Malicious Loopback",
        kind="orchestrator",
        endpoint="http://127.0.0.1:8000/api",
        orchestrator={"connector": "paperclip"},
    )
    adapter = OrchestratorAdapter(record)

    # 1. Probe should gracefully reject self-target without raising uncaught error
    probe_res = adapter.probe()
    assert probe_res.ok is False
    assert "Self-target loopback rejected" in probe_res.detail

    # 2. Direct check raises ForbiddenTarget
    with pytest.raises(ForbiddenTarget):
        adapter._check_self_target("http://localhost:8000")

    # 3. Call execution should reject self-target cleanly
    call_res = adapter.call("list_agents", {})
    assert call_res.ok is False
    assert "Self-target loopback rejected" in (call_res.error or "")


def test_paperclip_probe_and_verbs():
    server, endpoint = _start_mock_paperclip()
    try:
        record = AgentRecord(
            id="orchestrator_paperclip",
            display_name="Paperclip Fleet",
            kind="orchestrator",
            endpoint=endpoint,
            orchestrator={"connector": "paperclip"},
        )
        adapter = OrchestratorAdapter(record)

        # Probe test
        probe = adapter.probe()
        assert probe.ok is True
        assert probe.stage == "capability"

        # Verbs & schema
        verbs = adapter.verbs()
        verb_map = {v.name: v for v in verbs}
        assert "list_agents" in verb_map
        assert "get_status" in verb_map
        assert "create_issue" in verb_map
        assert "run_heartbeat" in verb_map

        assert verb_map["list_agents"].mutating is False
        assert verb_map["create_issue"].mutating is True
        assert verb_map["run_heartbeat"].mutating is True

        # Call read-only verb
        res_agents = adapter.call("list_agents", {})
        assert res_agents.ok is True
        assert "coder-alpha" in res_agents.output
        assert isinstance(res_agents.data, dict)

        # Call mutating verb
        res_issue = adapter.call("create_issue", {"title": "Fix memory leak", "priority": "high"})
        assert res_issue.ok is True
        assert "issue-99" in res_issue.output
        assert "Fix memory leak" in res_issue.output

        # Call heartbeat
        res_hb = adapter.call("run_heartbeat", {})
        assert res_hb.ok is True
        assert "pulse-123" in res_hb.output
    finally:
        server.shutdown()


def test_orca_probe_and_verbs(tmp_path):
    script = _make_mock_orca_script(tmp_path)
    record = AgentRecord(
        id="orchestrator_orca",
        display_name="Orca ADE",
        kind="orchestrator",
        executable=f"{sys.executable} {script}",
        transport=AgentTransport(argv=[sys.executable, str(script)]),
        orchestrator={"connector": "orca"},
    )
    adapter = OrchestratorAdapter(record)
    # Patch executable to run via python script wrapper
    adapter._executable = f"{sys.executable} {script}"

    verbs = adapter.verbs()
    verb_map = {v.name: v for v in verbs}
    assert "list_worktrees" in verb_map
    assert "assign_goal" in verb_map
    assert verb_map["list_worktrees"].mutating is False
    assert verb_map["assign_goal"].mutating is True


def test_to_tool_specs_and_runtime_execution(tmp_path):
    server, endpoint = _start_mock_paperclip()
    try:
        record = AgentRecord(
            id="test_paperclip",
            display_name="Test Paperclip",
            kind="orchestrator",
            endpoint=endpoint,
            orchestrator={"connector": "paperclip"},
        )
        adapter = OrchestratorAdapter(record)
        specs = adapter.to_tool_specs()
        assert len(specs) == 4

        spec_map = {s.name: s for s in specs}
        assert "agent_test_paperclip_list_agents" in spec_map
        assert "agent_test_paperclip_create_issue" in spec_map

        read_spec = spec_map["agent_test_paperclip_list_agents"]
        write_spec = spec_map["agent_test_paperclip_create_issue"]

        assert read_spec.requires_approval is False
        assert read_spec.tool_class == "read"

        assert write_spec.requires_approval is True
        assert write_spec.tool_class == "write"

        # Register and execute through AgentRuntime
        for s in specs:
            register_spec(s)
        try:
            runtime = AgentRuntime(
                workspace=tmp_path,
                policy=PermissionPolicy.from_dict({
                    "classes": {"read": "allow", "write": "allow"},
                    "headless": {"read": "allow", "write": "allow"},
                }),
            )
            out = runtime.execute("agent_test_paperclip_list_agents", {})
            assert "coder-alpha" in out
        finally:
            for s in specs:
                unregister_spec(s.name)
    finally:
        server.shutdown()


def test_bridge_discover_and_publish_orchestrator(tmp_path, monkeypatch):
    reg_file = tmp_path / "registry.json"
    registry = AgentRegistry(store_path=reg_file)
    bridge = AgentBridge(registry=registry, artifacts_dir=tmp_path / "artifacts")

    server, endpoint = _start_mock_paperclip()
    try:
        fake_rec = AgentRecord(
            id="orchestrator_paperclip",
            display_name="Paperclip Fleet",
            kind="orchestrator",
            endpoint=endpoint,
            orchestrator={"connector": "paperclip"},
        )
        monkeypatch.setattr("core.agent_bridge.bridge.run_all", lambda _ctx: [fake_rec])

        discovered = bridge.discover(workspace=tmp_path)
        assert len(discovered) == 1
        assert "orchestrator_paperclip" in bridge._adapters

        published = bridge.publish_tools()
        assert "agent_orchestrator_paperclip_list_agents" in published
        assert "agent_orchestrator_paperclip_create_issue" in published

        spec = get_spec("agent_orchestrator_paperclip_list_agents")
        assert spec is not None
        assert spec.tool_class == "read"

        bridge.close()
        assert get_spec("agent_orchestrator_paperclip_list_agents") is None
    finally:
        server.shutdown()
