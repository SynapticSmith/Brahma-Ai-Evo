"""Golden contract tests for AgentAdapter implementations (ADR-018, Q2)."""

from pathlib import Path
import sys

from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from core.agent_bridge.adapters.mcp_server import MCPServerAdapter
from core.agent_bridge.models import AgentRecord, AgentTransport

FAKE_MCP_SERVER = r'''
import json
import sys

def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        req = json.loads(line)
        method = req.get("method")
        msg_id = req.get("id")

        if method == "initialize":
            res = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-server", "version": "1.0"},
            }
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": res}) + "\n")
            sys.stdout.flush()
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            tools = [
                {
                    "name": "echo",
                    "description": "Echo input text",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                },
                {
                    "name": "add",
                    "description": "Add two numbers",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number"},
                            "b": {"type": "number"},
                        },
                        "required": ["a", "b"],
                    },
                },
            ]
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}) + "\n")
            sys.stdout.flush()
        elif method == "tools/call":
            params = req.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})
            if name == "echo":
                text = str(args.get("text", ""))
                sys.stdout.write(json.dumps({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"content": [{"type": "text", "text": f"Echo: {text}"}]},
                }) + "\n")
                sys.stdout.flush()
            elif name == "add":
                val = float(args.get("a", 0)) + float(args.get("b", 0))
                sys.stdout.write(json.dumps({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"content": [{"type": "text", "text": str(val)}]},
                }) + "\n")
                sys.stdout.flush()
            else:
                sys.stdout.write(json.dumps({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"content": [{"type": "text", "text": "Unknown tool"}], "isError": True},
                }) + "\n")
                sys.stdout.flush()

if __name__ == "__main__":
    main()
'''


def _make_fake_server_record(tmp_path: Path) -> AgentRecord:
    server_script = tmp_path / "fake_mcp_server.py"
    server_script.write_text(FAKE_MCP_SERVER, encoding="utf-8")

    return AgentRecord(
        id="fake_mcp",
        display_name="Fake MCP Server",
        kind="mcp_server",
        executable=sys.executable,
        transport=AgentTransport(
            mode="mcp_stdio",
            argv=[sys.executable, str(server_script)],
        ),
        sources=["config"],
    )


def test_mcp_adapter_probe_success(tmp_path):
    record = _make_fake_server_record(tmp_path)
    adapter = MCPServerAdapter(record)

    probe = adapter.probe()
    assert probe.ok is True
    assert probe.stage == "capability"
    assert "echo" in probe.models
    assert "add" in probe.models


def test_mcp_adapter_probe_missing_executable():
    record = AgentRecord(
        id="broken",
        display_name="Broken",
        kind="mcp_server",
        executable="C:\\nonexistent\\binary.exe",
        transport=AgentTransport(mode="mcp_stdio", argv=["C:\\nonexistent\\binary.exe"]),
    )
    adapter = MCPServerAdapter(record)
    probe = adapter.probe()
    assert probe.ok is False
    assert probe.stage == "liveness"


def test_mcp_adapter_connect_and_verbs(tmp_path):
    record = _make_fake_server_record(tmp_path)
    adapter = MCPServerAdapter(record)

    try:
        adapter.connect()
        assert adapter.record.status == "connected"

        verbs = adapter.verbs()
        verb_names = {v.name for v in verbs}
        assert "echo" in verb_names
        assert "add" in verb_names
    finally:
        adapter.close()
    assert adapter.record.status == "found"


def test_mcp_adapter_call_success(tmp_path):
    record = _make_fake_server_record(tmp_path)
    adapter = MCPServerAdapter(record)

    try:
        adapter.connect()
        result = adapter.call("echo", {"text": "Hello World!"})
        assert result.ok is True
        assert "Echo: Hello World!" in result.output
        assert result.error is None

        result2 = adapter.call("add", {"a": 15, "b": 27})
        assert result2.ok is True
        assert "42" in result2.output
    finally:
        adapter.close()


def test_mcp_adapter_degraded_state():
    # Record with invalid command
    record = AgentRecord(
        id="crash_server",
        display_name="Crash Server",
        kind="mcp_server",
        executable=sys.executable,
        transport=AgentTransport(mode="mcp_stdio", argv=[sys.executable, "-c", "import sys; sys.exit(1)"]),
    )
    adapter = MCPServerAdapter(record)

    result = adapter.call("any_verb", {})
    # Must fail cleanly with ok=False without raising uncaught exception
    assert result.ok is False
    assert result.error is not None
    assert adapter.record.status == "error"


def test_mcp_adapter_to_tool_specs_and_runtime_execution(tmp_path):
    record = _make_fake_server_record(tmp_path)
    adapter = MCPServerAdapter(record)

    try:
        adapter.connect()
        specs = adapter.to_tool_specs()
        assert len(specs) == 2

        spec_map = {s.name: s for s in specs}
        assert "agent_fake_mcp_echo" in spec_map
        assert "agent_fake_mcp_add" in spec_map

        echo_spec = spec_map["agent_fake_mcp_echo"]
        assert echo_spec.tool_class == "mcp"
        assert "[AGENT:Fake MCP Server]" in echo_spec.description

        # Test execution through AgentRuntime.execute
        from agent.spec import register, unregister
        register(echo_spec)
        try:
            runtime = AgentRuntime(workspace=tmp_path, policy=PermissionPolicy.from_dict({"classes": {"mcp": "allow"}}))
            out = runtime.execute(echo_spec.name, {"text": "Runtime Dispatch Test"})
            assert "Echo: Runtime Dispatch Test" in out
        finally:
            unregister(echo_spec.name)
    finally:
        adapter.close()


def test_mcp_adapter_close_is_idempotent(tmp_path):
    record = _make_fake_server_record(tmp_path)
    adapter = MCPServerAdapter(record)
    adapter.connect()

    # Repeated close calls must not raise
    adapter.close()
    adapter.close()
    assert adapter._server is None
