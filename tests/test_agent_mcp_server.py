"""tests/test_agent_mcp_server.py — Unit tests for the outbound Brahma MCP server."""

import io
import json
from pathlib import Path

import pytest

from agent.mcp_server import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    SERVER_NAME,
    MCPServer,
)
from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hello.txt").write_text("Hello MCP world!", encoding="utf-8")
    (ws / "AGENTS.md").write_text("# Test Instructions\nFollow these rules.", encoding="utf-8")

    skills_dir = ws / ".brahma" / "skills" / "sample-task"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: sample-task\ndescription: A sample task playbook\n---\nRun the sample step-by-step.",
        encoding="utf-8",
    )
    return ws


@pytest.fixture
def server(workspace: Path) -> MCPServer:
    policy = PermissionPolicy.load(None)
    policy.data["classes"] = {"read": "allow", "write": "allow", "exec": "deny"}
    runtime = AgentRuntime(workspace=workspace, policy=policy, headless=True)
    return MCPServer(runtime=runtime, workspace=workspace)


def test_handshake_and_ping(server: MCPServer) -> None:
    # 1. Initialize
    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    }
    resp = server.handle_request(init_req)
    assert resp is not None
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    result = resp["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == SERVER_NAME
    assert "tools" in result["capabilities"]
    assert "prompts" in result["capabilities"]
    assert "resources" in result["capabilities"]

    # 2. Initialized notification (no response expected)
    notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert server.handle_request(notif) is None
    assert server.initialized is True

    # 3. Ping
    ping_req = {"jsonrpc": "2.0", "id": "ping-1", "method": "ping"}
    ping_resp = server.handle_request(ping_req)
    assert ping_resp == {"jsonrpc": "2.0", "id": "ping-1", "result": {}}


def test_jsonrpc_protocol_errors(server: MCPServer) -> None:
    # Not an object
    assert server.handle_request(["bad"])["error"]["code"] == INVALID_REQUEST  # type: ignore[arg-type]

    # Wrong version
    assert server.handle_request({"jsonrpc": "1.0", "id": 1, "method": "ping"})["error"]["code"] == INVALID_REQUEST

    # Missing method
    assert server.handle_request({"jsonrpc": "2.0", "id": 1})["error"]["code"] == INVALID_REQUEST

    # Non-dict params
    assert server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": "string"})["error"]["code"] == INVALID_PARAMS

    # Unknown method
    unknown = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "non_existent_method"})
    assert unknown["error"]["code"] == METHOD_NOT_FOUND

    # JSON parse error via handle_line
    parse_err = json.loads(server.handle_line("{malformed-json"))
    assert parse_err["error"]["code"] == PARSE_ERROR

    # Blank line returns None
    assert server.handle_line("   \n") is None


def test_tools_list(server: MCPServer) -> None:
    req = {"jsonrpc": "2.0", "id": 10, "method": "tools/list"}
    resp = server.handle_request(req)
    assert resp is not None
    assert resp["id"] == 10
    tools = resp["result"]["tools"]
    assert isinstance(tools, list)
    assert len(tools) > 0

    tool_names = {t["name"] for t in tools}
    assert "read_file" in tool_names
    assert "write_file" in tool_names
    assert "edit_file" in tool_names
    assert "glob_files" in tool_names
    assert "grep_files" in tool_names

    # Check structure
    read_file = next(t for t in tools if t["name"] == "read_file")
    assert "description" in read_file
    assert read_file["inputSchema"]["type"] == "object"
    assert "path" in read_file["inputSchema"]["properties"]


def test_tools_call_success_and_denial(server: MCPServer) -> None:
    # 1. Allowed tool call (read_file)
    call_req = {
        "jsonrpc": "2.0",
        "id": 20,
        "method": "tools/call",
        "params": {
            "name": "read_file",
            "arguments": {"path": "hello.txt"},
        },
    }
    resp = server.handle_request(call_req)
    assert resp is not None
    assert resp["id"] == 20
    assert resp["result"]["isError"] is False
    assert "Hello MCP world!" in resp["result"]["content"][0]["text"]

    # 2. Denied tool call (run_command denied by policy)
    deny_req = {
        "jsonrpc": "2.0",
        "id": 21,
        "method": "tools/call",
        "params": {
            "name": "run_command",
            "arguments": {"command": "echo test"},
        },
    }
    deny_resp = server.handle_request(deny_req)
    assert deny_resp is not None
    assert deny_resp["id"] == 21
    assert deny_resp["result"]["isError"] is True
    assert "permission denied" in deny_resp["result"]["content"][0]["text"].lower()

    # 3. Missing name
    missing_name = server.handle_request({
        "jsonrpc": "2.0",
        "id": 22,
        "method": "tools/call",
        "params": {"arguments": {}},
    })
    assert missing_name["error"]["code"] == INVALID_PARAMS


def test_prompts_list_and_get(server: MCPServer) -> None:
    # List prompts
    list_req = {"jsonrpc": "2.0", "id": 30, "method": "prompts/list"}
    resp = server.handle_request(list_req)
    assert resp is not None
    prompts = resp["result"]["prompts"]
    prompt_names = {p["name"] for p in prompts}
    assert "sample-task" in prompt_names

    # Get prompt
    get_req = {
        "jsonrpc": "2.0",
        "id": 31,
        "method": "prompts/get",
        "params": {"name": "sample-task"},
    }
    get_resp = server.handle_request(get_req)
    assert get_resp is not None
    assert get_resp["id"] == 31
    assert "Run the sample step-by-step" in get_resp["result"]["messages"][0]["content"]["text"]

    # Not found prompt
    not_found = server.handle_request({
        "jsonrpc": "2.0",
        "id": 32,
        "method": "prompts/get",
        "params": {"name": "does-not-exist"},
    })
    assert not_found["error"]["code"] == INVALID_PARAMS


def test_resources_list_and_read(server: MCPServer, workspace: Path) -> None:
    # List resources
    list_req = {"jsonrpc": "2.0", "id": 40, "method": "resources/list"}
    resp = server.handle_request(list_req)
    assert resp is not None
    resources = resp["result"]["resources"]
    assert any(r["name"] == "AGENTS.md" for r in resources)

    agents_res = next(r for r in resources if r["name"] == "AGENTS.md")
    uri = agents_res["uri"]

    # Read resource
    read_req = {
        "jsonrpc": "2.0",
        "id": 41,
        "method": "resources/read",
        "params": {"uri": uri},
    }
    read_resp = server.handle_request(read_req)
    assert read_resp is not None
    contents = read_resp["result"]["contents"]
    assert len(contents) == 1
    assert contents[0]["mimeType"] == "text/markdown"
    assert "Follow these rules" in contents[0]["text"]

    # Read resource outside workspace
    outside_file = workspace.parent / "outside.txt"
    outside_file.write_text("secret", encoding="utf-8")
    outside_uri = outside_file.resolve().as_uri()

    outside_resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 42,
        "method": "resources/read",
        "params": {"uri": outside_uri},
    })
    assert outside_resp["error"]["code"] == INVALID_PARAMS
    assert "outside workspace" in outside_resp["error"]["message"].lower()


def test_serve_stream(server: MCPServer) -> None:
    input_lines = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n"
    )
    reader = io.StringIO(input_lines)
    writer = io.StringIO()

    server.serve(reader, writer)

    output = writer.getvalue().strip().split("\n")
    # Should have exactly two responses (initialized notification yields no response)
    assert len(output) == 2
    r1 = json.loads(output[0])
    assert r1["id"] == 1
    r2 = json.loads(output[1])
    assert r2["id"] == 2


def test_serve_forces_utf8_and_lf_framing(server: MCPServer, workspace: Path) -> None:
    """A legacy Windows code page must not mangle or crash non-ASCII output."""
    (workspace / "arrow.txt").write_text("rule \u21d2 note", encoding="utf-8")
    uri = (workspace / "arrow.txt").resolve().as_uri()
    request = json.dumps({
        "jsonrpc": "2.0",
        "id": 7,
        "method": "resources/read",
        "params": {"uri": uri},
    }) + "\n"

    raw = io.BytesIO()
    writer = io.TextIOWrapper(raw, encoding="cp1252", newline="\r\n")
    server.serve(io.StringIO(request), writer)
    writer.flush()
    data = raw.getvalue()

    assert b"\r\n" not in data
    assert "\u21d2".encode("utf-8") in data
    assert json.loads(data.decode("utf-8"))["id"] == 7


def test_cli_mcp_server_invocation(workspace: Path) -> None:
    import sys
    from agent.__main__ import main

    ping_req = json.dumps({"jsonrpc": "2.0", "id": "cli-1", "method": "ping"}) + "\n"
    stdin = io.StringIO(ping_req)
    stdout = io.StringIO()

    orig_stdin, orig_stdout = sys.stdin, sys.stdout
    try:
        sys.stdin, sys.stdout = stdin, stdout
        ret = main(["mcp-server", "--cwd", str(workspace), "--yolo"])
        assert ret == 0
    finally:
        sys.stdin, sys.stdout = orig_stdin, orig_stdout

    lines = stdout.getvalue().strip().split("\n")
    assert len(lines) == 1
    resp = json.loads(lines[0])
    assert resp["id"] == "cli-1"
    assert resp["result"] == {}

