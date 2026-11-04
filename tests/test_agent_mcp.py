"""MCP client: config validation, discovery, naming, calls and lifecycle.

Each test runs a tiny fake server (a Python script) over a real pipe, so the
JSON-RPC framing, reader thread, timeouts and shutdown are exercised rather
than mocked. The harness CI installs only pytest, which is exactly why the
client is stdlib-only; no ``mcp`` SDK is imported anywhere here.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from agent.mcp import (
    MCPConfigError,
    MCPError,
    MCPServerConfig,
    MCPSession,
    MCPTimeout,
    WorkspaceMCP,
    discover_config,
    format_prompt_result,
    format_resource_result,
    format_tool_result,
    load_config,
    mcp_openai_tools,
    parse_servers,
    tool_spec_name,
    with_mcp_tools,
)
from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from agent.spec import all_specs, get as get_spec

FAKE_SERVER = r'''
import json
import os
import sys
import time

TOOLS = [
    {
        "name": "echo",
        "description": "Echo the arguments back as JSON.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {"name": "fail", "description": "Always fails.", "inputSchema": {"type": "object"}},
    {"name": "slow", "description": "Never answers in time.", "inputSchema": {"type": "object"}},
    {"name": "binary", "description": "Image plus caption.", "inputSchema": {"type": "object"}},
    {"name": "resource", "description": "A text resource.", "inputSchema": {"type": "object"}},
    {"name": "structured", "description": "Structured content.", "inputSchema": {"type": "object"}},
    {"name": "crash", "description": "Exit the server.", "inputSchema": {"type": "object"}},
    {"name": "pid", "description": "This process's id.", "inputSchema": {"type": "object"}},
    {"name": "mutate", "description": "Change the tool list and notify.", "inputSchema": {"type": "object"}},
    {"name": "break_listing", "description": "Make tools/list fail.", "inputSchema": {"type": "object"}},
]

LISTING_BROKEN = False


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def notify(method):
    send({"jsonrpc": "2.0", "method": method})


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except ValueError:
        continue
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        continue
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1.0"},
        }
    elif method == "tools/list":
        if LISTING_BROKEN:
            sys.stderr.write("fake server list failure\n")
            sys.stderr.flush()
            # Give the client's stderr reader a moment so the failure carries
            # the tail, the same way a real server's crash report would.
            time.sleep(0.1)
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32603, "message": "listing is broken"},
                }
            )
            continue
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "echo":
            result = {"content": [{"type": "text", "text": json.dumps(arguments, sort_keys=True)}]}
        elif name == "fail":
            result = {"content": [{"type": "text", "text": "boom"}], "isError": True}
        elif name == "slow":
            time.sleep(10)
            result = {"content": [{"type": "text", "text": "late"}]}
        elif name == "binary":
            result = {
                "content": [
                    {"type": "image", "mimeType": "image/png", "data": "QUJD"},
                    {"type": "text", "text": "caption"},
                ]
            }
        elif name == "resource":
            result = {
                "content": [
                    {
                        "type": "resource",
                        "resource": {
                            "uri": "file:///x.txt",
                            "mimeType": "text/plain",
                            "text": "resource-body",
                        },
                    }
                ]
            }
        elif name == "structured":
            result = {
                "content": [{"type": "text", "text": "ok"}],
                "structuredContent": {"count": 2},
            }
        elif name == "crash":
            sys.stderr.write("fake server crashing\n")
            sys.stderr.flush()
            os._exit(3)
        elif name == "pid":
            result = {"content": [{"type": "text", "text": str(os.getpid())}]}
        elif name == "mutate":
            for tool in list(TOOLS):
                if tool["name"] == "binary":
                    TOOLS.remove(tool)
            if not any(tool["name"] == "added" for tool in TOOLS):
                TOOLS.append(
                    {
                        "name": "added",
                        "description": "Appeared after list_changed.",
                        "inputSchema": {"type": "object"},
                    }
                )
            notify("notifications/tools/list_changed")
            result = {"content": [{"type": "text", "text": "mutated"}]}
        elif name == "break_listing":
            LISTING_BROKEN = True
            notify("notifications/tools/list_changed")
            result = {"content": [{"type": "text", "text": "broken"}]}
        else:
            result = {"content": [{"type": "text", "text": "unknown tool"}], "isError": True}
    else:
        send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "no"}})
        continue
    send({"jsonrpc": "2.0", "id": request_id, "result": result})
'''


def _script(tmp_path: Path) -> Path:
    path = tmp_path / "fake_mcp.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return path


def _config(
    tmp_path: Path,
    *,
    name: str = "demo",
    timeout: float = 10.0,
    entry: dict | None = None,
) -> Path:
    script = _script(tmp_path)
    server = entry or {
        "command": sys.executable,
        "args": [str(script)],
        "timeout": timeout,
    }
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {name: server}}), encoding="utf-8")
    return path


def _session(tmp_path: Path, **kwargs) -> MCPSession:
    session = MCPSession()
    session.start(load_config(_config(tmp_path, **kwargs)))
    return session


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

def test_a_minimal_server_gets_documented_defaults():
    configs = parse_servers({"mcpServers": {"demo": {"command": "npx"}}})

    assert len(configs) == 1
    config = configs[0]
    assert isinstance(config, MCPServerConfig)
    assert config.argv() == ["npx"]
    assert config.timeout == 60.0
    assert config.env == {}


def test_a_disabled_server_is_skipped():
    configs = parse_servers(
        {"mcpServers": {"off": {"command": "npx", "enabled": False}, "on": {"command": "node"}}}
    )

    assert [config.name for config in configs] == ["on"]


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"mcpServers": []},
        {"mcpServers": {"x": "npx"}},
        {"mcpServers": {"x": {}}},
        {"mcpServers": {"x": {"command": ""}}},
        {"mcpServers": {"x": {"command": "npx", "args": "foo"}}},
        {"mcpServers": {"x": {"command": "npx", "args": [1]}}},
        {"mcpServers": {"x": {"command": "npx", "env": {"A": 1}}}},
        {"mcpServers": {"x": {"command": "npx", "env": {"": "a"}}}},
        {"mcpServers": {"x": {"command": "npx", "timeout": 0}}},
        {"mcpServers": {"x": {"command": "npx", "timeout": "60"}}},
        {"mcpServers": {"x": {"command": "npx", "enabled": "yes"}}},
        {"mcpServers": {"": {"command": "npx"}}},
    ],
)
def test_malformed_config_is_rejected_with_a_path(document):
    with pytest.raises(MCPConfigError):
        parse_servers(document, source="demo.json")


def test_load_config_resolves_a_relative_cwd_next_to_the_file(tmp_path: Path):
    path = tmp_path / "nested" / "mcp.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps({"mcpServers": {"x": {"command": "npx", "cwd": "work"}}}),
        encoding="utf-8",
    )

    configs = load_config(path)

    assert configs[0].cwd == str((path.parent / "work").resolve())


def test_a_missing_file_is_an_error_not_an_empty_list(tmp_path: Path):
    with pytest.raises(MCPConfigError):
        load_config(tmp_path / "absent.json")


def test_discovery_prefers_the_explicit_env_over_the_workspace(tmp_path: Path, monkeypatch):
    explicit = tmp_path / "explicit.json"
    explicit.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BRAHMA_MCP_CONFIG", str(explicit))

    assert discover_config(tmp_path) == explicit


def test_discovery_prefers_dot_brahma_over_dot_mcp(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("BRAHMA_MCP_CONFIG", raising=False)
    brahma = tmp_path / ".brahma" / "mcp.json"
    brahma.parent.mkdir()
    brahma.write_text("{}", encoding="utf-8")
    (tmp_path / ".mcp.json").write_text("{}", encoding="utf-8")

    assert discover_config(tmp_path) == brahma


def test_discovery_returns_none_when_nothing_is_configured(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("BRAHMA_MCP_CONFIG", raising=False)

    assert discover_config(tmp_path) is None


# --------------------------------------------------------------------------- #
# Naming
# --------------------------------------------------------------------------- #

def test_tool_names_are_namespaced_and_sanitized():
    assert tool_spec_name("demo", "read-file.v2") == "mcp_demo_read_file_v2"


def test_tool_names_with_several_illegal_characters_collapse_to_one_underscore():
    assert tool_spec_name("my server", "a  b::c") == "mcp_my_server_a_b_c"


def test_long_names_are_truncated_with_a_stable_digest():
    name = tool_spec_name("s" * 40, "t" * 60)

    assert len(name) <= 64
    assert name == tool_spec_name("s" * 40, "t" * 60)


def test_a_collision_gets_a_numeric_suffix():
    first = tool_spec_name("demo", "echo")
    second = tool_spec_name("demo", "echo", taken={first})

    assert second == first + "_2"
    assert second != first


# --------------------------------------------------------------------------- #
# Protocol
# --------------------------------------------------------------------------- #

def test_a_session_lists_tools_and_calls_one(tmp_path: Path):
    session = _session(tmp_path)
    try:
        names = session.tools
        assert "mcp_demo_echo" in names
        assert "mcp_demo_fail" in names
        assert get_spec("mcp_demo_echo") is not None

        result = session.call("mcp_demo_echo", {"text": "hello"})
        assert json.loads(result) == {"text": "hello"}
    finally:
        session.close()

    assert get_spec("mcp_demo_echo") is None
    assert not [spec for spec in all_specs() if spec.name.startswith("mcp_demo_")]


def test_the_spec_carries_the_description_and_schema(tmp_path: Path):
    session = _session(tmp_path)
    try:
        spec = get_spec("mcp_demo_echo")
        assert spec is not None
        assert spec.tool_class == "mcp"
        assert "Echo the arguments" in spec.description
        assert spec.parameters["properties"]["text"]["type"] == "string"
        assert spec.parameters["required"] == ["text"]
    finally:
        session.close()


def test_an_error_result_is_prefixed(tmp_path: Path):
    session = _session(tmp_path)
    try:
        assert session.call("mcp_demo_fail", {}) == "error: boom"
    finally:
        session.close()


def test_binary_content_is_summarised_and_text_kept(tmp_path: Path):
    session = _session(tmp_path)
    try:
        result = session.call("mcp_demo_binary", {})
        assert "[image image/png, 4 base64 chars]" in result
        assert "caption" in result
    finally:
        session.close()


def test_a_text_resource_is_inlined(tmp_path: Path):
    session = _session(tmp_path)
    try:
        assert session.call("mcp_demo_resource", {}) == "resource-body"
    finally:
        session.close()


def test_structured_content_is_appended(tmp_path: Path):
    session = _session(tmp_path)
    try:
        result = session.call("mcp_demo_structured", {})
        assert "ok" in result
        assert '"count": 2' in result
    finally:
        session.close()


def test_a_slow_call_times_out_without_hanging_the_harness(tmp_path: Path):
    session = _session(tmp_path, timeout=0.3)
    try:
        started = time.monotonic()
        with pytest.raises(MCPTimeout):
            session.call("mcp_demo_slow", {})
        assert time.monotonic() - started < 5
    finally:
        session.close()


def test_a_crashing_server_fails_the_call_and_reports_stderr(tmp_path: Path):
    session = _session(tmp_path)
    try:
        with pytest.raises(MCPError) as caught:
            session.call("mcp_demo_crash", {})
        assert "exited" in str(caught.value).lower()
        assert "fake server crashing" in str(caught.value)
    finally:
        session.close()


def test_a_tool_call_after_close_is_refused(tmp_path: Path):
    session = _session(tmp_path)
    session.close()

    with pytest.raises(MCPError):
        session.call("mcp_demo_echo", {})


def test_a_server_that_cannot_start_is_reported_per_server(tmp_path: Path):
    config = MCPServerConfig(name="missing", command="definitely-not-a-real-command-xyz")
    session = MCPSession()

    statuses = session.start([config])
    session.close()

    assert len(statuses) == 1
    assert statuses[0].ok is False
    assert "could not start" in statuses[0].error


def test_start_status_reports_the_tool_count(tmp_path: Path):
    session = _session(tmp_path)
    try:
        assert session.statuses[0].ok is True
        assert session.statuses[0].tools == 10
        assert session.statuses[0].name == "demo"
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Policy and runtime integration
# --------------------------------------------------------------------------- #

def _runtime(tmp_path: Path, *, classes: dict | None = None) -> AgentRuntime:
    return AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict(
            {"classes": classes or {"mcp": "allow", "read": "allow", "write": "allow"}}
        ),
        approver=None,
        headless=True,
    )


def test_mcp_tools_run_through_the_single_runtime_boundary(tmp_path: Path):
    session = _session(tmp_path)
    try:
        runtime = _runtime(tmp_path)
        result = runtime.execute("mcp_demo_echo", {"text": "from-runtime"})
        assert json.loads(result) == {"text": "from-runtime"}
    finally:
        session.close()


def test_the_mcp_class_can_be_denied_by_policy(tmp_path: Path):
    session = _session(tmp_path)
    try:
        runtime = _runtime(tmp_path, classes={"mcp": "deny"})
        result = runtime.execute("mcp_demo_echo", {"text": "nope"})
        assert result.lower().startswith("permission denied")
    finally:
        session.close()


def test_one_tool_can_be_overridden_by_name(tmp_path: Path):
    session = _session(tmp_path)
    try:
        runtime = AgentRuntime(
            workspace=tmp_path,
            policy=PermissionPolicy.from_dict(
                {"classes": {"mcp": "allow"}, "tools": {"mcp_demo_echo": "deny"}}
            ),
            approver=None,
            headless=True,
        )
        result = runtime.execute("mcp_demo_echo", {"text": "nope"})
        assert result.lower().startswith("permission denied")
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #

def test_unknown_content_shapes_are_json_rendered():
    rendered = format_tool_result({"content": [{"type": "widget", "id": 7}]})

    assert '"id": 7' in rendered


def test_missing_content_is_not_silently_empty():
    assert format_tool_result({"content": []}) == "(no content)"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def test_the_cli_lists_servers_and_tools(tmp_path: Path, capsys):
    from agent.__main__ import main

    config = _config(tmp_path)
    code = main(["mcp", "list", "--config", str(config), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["servers"][0]["ok"] is True
    assert payload["servers"][0]["tools"] == 10
    assert "mcp_demo_echo" in payload["tools"]


def test_the_cli_without_config_explains_how_to_configure(tmp_path: Path, monkeypatch, capsys):
    from agent.__main__ import main

    monkeypatch.delenv("BRAHMA_MCP_CONFIG", raising=False)
    code = main(["mcp", "list", "--cwd", str(tmp_path)])

    assert code == 0
    assert "No MCP config found" in capsys.readouterr().out


def test_a_broken_explicit_config_stops_a_run_instead_of_dropping_tools(tmp_path: Path, capsys):
    from agent.__main__ import main

    bad = tmp_path / "bad.json"
    bad.write_text('{"mcpServers": {"x": {"command": ""}}}', encoding="utf-8")

    code = main(
        [
            "run",
            "hi",
            "--provider",
            "stub",
            "--no-session",
            "--mcp-config",
            str(bad),
        ]
    )

    assert code == 3
    assert "command" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# GUI workspace manager
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _isolated_user_data(tmp_path: Path, monkeypatch):
    """Keep workspace trust records isolated per test."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "_localappdata"))


def _workspace_config(root: Path, name: str = "demo", *, trusted: bool = True) -> Path:
    """Write ``<root>/.brahma/mcp.json``, the GUI's discovery location."""
    if trusted:
        from core.user_paths import trust_workspace

        trust_workspace(root)
    script = _script(root)
    path = root / ".brahma" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    name: {"command": sys.executable, "args": [str(script)], "timeout": 10}
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def test_the_workspace_manager_starts_once_and_reuses_the_session(tmp_path: Path):
    _workspace_config(tmp_path)
    manager = WorkspaceMCP()
    try:
        assert manager.ensure(tmp_path) is True
        assert manager.ensure(tmp_path) is True

        assert manager.generation == 1
        assert "mcp_demo_echo" in manager.tools
        assert manager.statuses[0].ok is True
    finally:
        manager.close()


def test_no_config_is_healthy_and_starts_nothing(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("BRAHMA_MCP_CONFIG", raising=False)
    manager = WorkspaceMCP()
    try:
        assert manager.ensure(tmp_path) is True
        assert manager.tools == []
        assert manager.generation == 0
        assert manager.ok is True
    finally:
        manager.close()


def test_a_workspace_change_restarts_and_unregisters_old_tools(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _workspace_config(first, "alpha")
    _workspace_config(second, "beta")
    manager = WorkspaceMCP()
    try:
        assert manager.ensure(first) is True
        assert get_spec("mcp_alpha_echo") is not None

        assert manager.ensure(second) is True

        assert get_spec("mcp_alpha_echo") is None
        assert get_spec("mcp_beta_echo") is not None
        assert manager.generation == 2
    finally:
        manager.close()


def test_a_config_change_restarts_without_a_workspace_change(tmp_path: Path):
    _workspace_config(tmp_path, "alpha")
    manager = WorkspaceMCP()
    try:
        assert manager.ensure(tmp_path) is True
        assert get_spec("mcp_alpha_echo") is not None

        _workspace_config(tmp_path, "renamed")

        assert manager.ensure(tmp_path) is True
        assert get_spec("mcp_alpha_echo") is None
        assert get_spec("mcp_renamed_echo") is not None
        assert manager.generation == 2
    finally:
        manager.close()


def test_a_broken_config_is_reported_once_and_recovers_when_fixed(tmp_path: Path):
    path = tmp_path / ".brahma" / "mcp.json"
    path.parent.mkdir()
    path.write_text("{not json", encoding="utf-8")
    events: list = []
    manager = WorkspaceMCP()
    try:
        assert manager.ensure(tmp_path, on_event=events.append) is False
        assert manager.ok is False
        assert "invalid JSON" in manager.error
        assert events and events[0]["type"] == "error"

        # Same broken file, same fingerprint: no second event, still False.
        assert manager.ensure(tmp_path, on_event=events.append) is False
        assert len(events) == 1

        _workspace_config(tmp_path, "demo")

        assert manager.ensure(tmp_path, on_event=events.append) is True
        assert manager.ok is True
        assert "mcp_demo_echo" in manager.tools
    finally:
        manager.close()


def test_closing_the_workspace_manager_unregisters_its_tools(tmp_path: Path):
    _workspace_config(tmp_path)
    manager = WorkspaceMCP()
    assert manager.ensure(tmp_path) is True
    assert get_spec("mcp_demo_echo") is not None

    manager.close()
    manager.close()  # idempotent: shutdown may run twice

    assert get_spec("mcp_demo_echo") is None
    assert mcp_openai_tools() == []


def test_the_openai_tool_shape_matches_what_a_model_expects(tmp_path: Path):
    _workspace_config(tmp_path)
    manager = WorkspaceMCP()
    try:
        manager.ensure(tmp_path)
        tools = mcp_openai_tools()

        assert tools
        assert all(tool["type"] == "function" for tool in tools)
        assert all(tool["function"]["name"].startswith("mcp_demo_") for tool in tools)
        assert all("parameters" in tool["function"] for tool in tools)
    finally:
        manager.close()


def test_merging_appends_mcp_tools_without_duplicating_a_name(tmp_path: Path):
    _workspace_config(tmp_path)
    manager = WorkspaceMCP()
    try:
        manager.ensure(tmp_path)
        existing = {
            "type": "function",
            "function": {"name": "mcp_demo_echo", "description": "host copy", "parameters": {}},
        }
        other = {"name": "read_file", "description": "gemini shape"}

        merged = with_mcp_tools([existing, other])
        names = [
            (tool.get("function") or {}).get("name") or tool.get("name")
            for tool in merged
        ]

        assert names.count("mcp_demo_echo") == 1
        assert names[0] == "mcp_demo_echo"
        assert "read_file" in names
        assert "mcp_demo_fail" in names
    finally:
        manager.close()


def test_merging_is_a_no_op_when_nothing_is_registered():
    assert with_mcp_tools(None) == []
    assert with_mcp_tools([{"name": "read_file"}]) == [{"name": "read_file"}]


# --------------------------------------------------------------------------- #
# In-place refresh: tools/list_changed and a dead process
# --------------------------------------------------------------------------- #

def _call_spec(name: str, arguments: dict | None = None) -> str:
    """Invoke a registered tool through its spec handler, like the runtime does."""
    spec = get_spec(name)
    assert spec is not None, f"{name} is not registered"
    return spec.handler(arguments or {}, None)


def _two_server_config(root: Path, *, trusted: bool = True) -> Path:
    if trusted:
        from core.user_paths import trust_workspace

        trust_workspace(root)
    script = _script(root)
    path = root / ".brahma" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    name: {"command": sys.executable, "args": [str(script)], "timeout": 10}
                    for name in ("alpha", "beta")
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def test_the_workspace_manager_does_not_refresh_a_healthy_session(tmp_path: Path):
    _workspace_config(tmp_path)
    events: list = []
    manager = WorkspaceMCP()
    try:
        assert manager.ensure(tmp_path, on_event=events.append) is True
        assert manager.ensure(tmp_path, on_event=events.append) is True

        assert [e for e in events if e["type"] == "mcp_refresh"] == []
        assert manager.generation == 1
    finally:
        manager.close()


def test_a_list_changed_notification_refreshes_the_catalogue(tmp_path: Path):
    _workspace_config(tmp_path)
    events: list = []
    manager = WorkspaceMCP()
    try:
        assert manager.ensure(tmp_path) is True
        assert get_spec("mcp_demo_added") is None
        assert get_spec("mcp_demo_binary") is not None

        # The server changes its list and sends notifications/tools/list_changed.
        assert _call_spec("mcp_demo_mutate") == "mutated"

        assert manager.ensure(tmp_path, on_event=events.append) is True

        assert get_spec("mcp_demo_added") is not None
        assert get_spec("mcp_demo_binary") is None
        assert _call_spec("mcp_demo_echo", {"text": "still"}) == '{"text": "still"}'

        refresh = [e for e in events if e["type"] == "mcp_refresh"]
        assert len(refresh) == 1
        assert refresh[0]["server"] == "demo"
        assert refresh[0]["restarted"] is False
        assert refresh[0]["added"] == ["added"]
        assert refresh[0]["removed"] == ["binary"]
        # Re-listing is not a restart, so the generation counter is unchanged.
        assert manager.generation == 1
    finally:
        manager.close()


def test_a_killed_server_is_restarted_on_the_next_ensure(tmp_path: Path):
    _workspace_config(tmp_path)
    events: list = []
    manager = WorkspaceMCP()
    try:
        assert manager.ensure(tmp_path) is True
        before = _call_spec("mcp_demo_pid")
        assert "failed" in _call_spec("mcp_demo_crash").lower()

        assert manager.ensure(tmp_path, on_event=events.append) is True

        after = _call_spec("mcp_demo_pid")
        assert after != before
        assert _call_spec("mcp_demo_echo", {"text": "back"}) == '{"text": "back"}'
        assert manager.generation == 2
        refresh = [e for e in events if e["type"] == "mcp_refresh"]
        assert refresh and refresh[0]["restarted"] is True
    finally:
        manager.close()


def test_only_the_changed_server_is_restarted(tmp_path: Path):
    _two_server_config(tmp_path)
    events: list = []
    manager = WorkspaceMCP()
    try:
        assert manager.ensure(tmp_path) is True
        alpha_before = _call_spec("mcp_alpha_pid")
        beta_before = _call_spec("mcp_beta_pid")

        _call_spec("mcp_alpha_mutate")
        assert manager.ensure(tmp_path, on_event=events.append) is True

        assert _call_spec("mcp_alpha_pid") == alpha_before
        assert _call_spec("mcp_beta_pid") == beta_before
        assert get_spec("mcp_alpha_binary") is None
        assert get_spec("mcp_beta_binary") is not None

        refresh = [e for e in events if e["type"] == "mcp_refresh"]
        assert [e["server"] for e in refresh] == ["alpha"]
        assert refresh[0]["restarted"] is False
    finally:
        manager.close()


def test_a_failed_refresh_drops_the_tools_and_reports_the_error(tmp_path: Path):
    _workspace_config(tmp_path)
    events: list = []
    manager = WorkspaceMCP()
    try:
        assert manager.ensure(tmp_path) is True
        assert get_spec("mcp_demo_echo") is not None
        assert _call_spec("mcp_demo_break_listing") == "broken"

        assert manager.ensure(tmp_path, on_event=events.append) is True

        # A stale catalogue is worse than none: the tools are gone, and the
        # failure is reported once with the server's stderr tail.
        assert get_spec("mcp_demo_echo") is None
        assert manager.tools == []
        errors = [e for e in events if e["type"] == "error"]
        assert errors and "list failure" in errors[0]["error"]
        assert manager.statuses[0].ok is False
        assert "listing is broken" in manager.statuses[0].error

        # The broken server is not respawned on every following turn.
        assert manager.ensure(tmp_path, on_event=events.append) is True
        assert len([e for e in events if e["type"] == "error"]) == 1
    finally:
        manager.close()


def test_a_refresh_leaves_no_duplicate_or_orphaned_specs(tmp_path: Path):
    _workspace_config(tmp_path)
    manager = WorkspaceMCP()
    try:
        assert manager.ensure(tmp_path) is True
        _call_spec("mcp_demo_mutate")
        assert manager.ensure(tmp_path) is True

        names = manager.tools
        assert len(names) == len(set(names))
        assert all(get_spec(name) is not None for name in names)
    finally:
        manager.close()

    assert all(get_spec(name) is None for name in names)


def test_untrusted_workspace_mcp_config_is_not_loaded_until_trusted_and_revokes_on_delete(
    tmp_path: Path, monkeypatch
):
    from core.user_paths import (
        is_workspace_trusted,
        revoke_workspace_trust,
        trust_workspace,
    )

    monkeypatch.delenv("BRAHMA_MCP_CONFIG", raising=False)
    repo = tmp_path / "cloned_repo"
    repo.mkdir()
    script = _script(repo)
    (repo / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "clone_srv": {
                        "command": sys.executable,
                        "args": [str(script)],
                        "timeout": 10,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    events: list = []
    manager = WorkspaceMCP()
    try:
        assert is_workspace_trusted(repo) is False
        assert manager.ensure(repo, on_event=events.append) is True
        assert manager.generation == 0
        assert manager.tools == []
        assert get_spec("mcp_clone_srv_echo") is None
        assert manager.untrusted_servers == ["clone_srv"]
        untrusted = [e for e in events if e.get("type") == "mcp_untrusted"]
        assert len(untrusted) == 1
        assert untrusted[0]["count"] == 1
        assert "wants to start 1 MCP server" in untrusted[0]["message"]

        # Trusting the workspace loads .mcp.json on the next turn without a restart.
        trust_workspace(repo)
        assert is_workspace_trusted(repo) is True
        assert manager.ensure(repo, on_event=events.append) is True
        assert manager.generation == 1
        assert "mcp_clone_srv_echo" in manager.tools
        assert get_spec("mcp_clone_srv_echo") is not None

        # Deleting the trust row revokes on the next turn without a restart.
        assert revoke_workspace_trust(repo) is True
        assert manager.ensure(repo, on_event=events.append) is True
        assert manager.tools == []
        assert get_spec("mcp_clone_srv_echo") is None
    finally:
        manager.close()


def test_workspace_mcp_trust_prompt_can_approve_and_persist_trust(
    tmp_path: Path, monkeypatch
):
    from core.user_paths import is_workspace_trusted

    monkeypatch.delenv("BRAHMA_MCP_CONFIG", raising=False)
    repo = tmp_path / "prompted_repo"
    repo.mkdir()
    _workspace_config(repo, "prompted", trusted=False)

    prompts: list = []

    def _approve(ws: Path, configs: list) -> bool:
        prompts.append((ws, [c.name for c in configs]))
        return True

    manager = WorkspaceMCP()
    try:
        assert manager.ensure(repo, trust_prompt=_approve) is True
        assert len(prompts) == 1
        assert prompts[0][1] == ["prompted"]
        assert is_workspace_trusted(repo) is True
        assert "mcp_prompted_echo" in manager.tools
    finally:
        manager.close()


def test_cli_run_gates_untrusted_repo_local_mcp_config(
    tmp_path: Path, monkeypatch, capsys
):
    from agent.__main__ import main
    from core.user_paths import trust_workspace

    monkeypatch.delenv("BRAHMA_MCP_CONFIG", raising=False)
    repo = tmp_path / "cli_clone"
    repo.mkdir()
    _workspace_config(repo, "cli_srv", trusted=False)

    code = main(
        [
            "run",
            "say hello",
            "--cwd",
            str(repo),
            "--provider",
            "stub",
            "--json",
            "--no-session",
        ]
    )
    out_lines = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    assert code == 0
    untrusted = [e for e in out_lines if e.get("type") == "mcp_untrusted"]
    assert len(untrusted) == 1
    assert untrusted[0]["servers"] == ["cli_srv"]
    result = [e for e in out_lines if e.get("type") == "result"][0]
    assert "tools=9" in result["text"]

    # Once trusted, the next CLI run loads the server's 10 tools (9 + 10 = 19).
    trust_workspace(repo)
    code2 = main(
        [
            "run",
            "say hello",
            "--cwd",
            str(repo),
            "--provider",
            "stub",
            "--json",
            "--no-session",
        ]
    )
    out_lines2 = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    assert code2 == 0
    result2 = [e for e in out_lines2 if e.get("type") == "result"][0]
    assert "tools=19" in result2["text"]


# --------------------------------------------------------------------------- #
# MCP Resources and Prompts (N6)
# --------------------------------------------------------------------------- #

FAKE_CAPABILITIES_SERVER = r'''
import json
import sys

RESOURCES = [
    {
        "uri": "memo://guidelines",
        "name": "Project Guidelines",
        "description": "Rules for the repository",
        "mimeType": "text/markdown",
    }
]

PROMPTS = [
    {
        "name": "review-code",
        "description": "Code review template",
        "arguments": [
            {"name": "path", "description": "File to review", "required": True}
        ],
    }
]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except ValueError:
        continue
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        continue
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "resources": {"listChanged": True},
                "prompts": {"listChanged": True},
            },
            "serverInfo": {"name": "caps-server", "version": "1.0"},
        }
    elif method == "tools/list":
        result = {"tools": [{"name": "ping", "description": "Ping tool", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "pong"}]}
    elif method == "resources/list":
        result = {"resources": RESOURCES}
    elif method == "resources/read":
        params = message.get("params") or {}
        uri = params.get("uri")
        if uri == "memo://guidelines":
            result = {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": "# Repository Guidelines\nClean code only."}]}
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "not found"}}) + "\n")
            sys.stdout.flush()
            continue
    elif method == "prompts/list":
        result = {"prompts": PROMPTS}
    elif method == "prompts/get":
        params = message.get("params") or {}
        name = params.get("name")
        if name == "review-code":
            result = {
                "description": "Review code against guidelines",
                "messages": [
                    {"role": "user", "content": {"type": "text", "text": "Please review the specified file for defects."}}
                ],
            }
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "prompt not found"}}) + "\n")
            sys.stdout.flush()
            continue
    else:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unsupported"}}) + "\n")
        sys.stdout.flush()
        continue

    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
    sys.stdout.flush()
'''


def test_format_resource_result_variants():
    # Text item
    res = {"contents": [{"uri": "memo://1", "mimeType": "text/plain", "text": "Hello text!"}]}
    assert format_resource_result(res) == "Hello text!"

    # Blob item
    res_blob = {"contents": [{"uri": "data://bin", "mimeType": "application/octet-stream", "blob": "QUJD"}]}
    assert "4 base64 chars" in format_resource_result(res_blob)

    # Empty contents
    assert format_resource_result({"contents": []}) == "(empty resource)"

    # Non-mapping
    assert format_resource_result("raw-string") == '"raw-string"'


def test_format_prompt_result_variants():
    # Description + messages
    prompt = {
        "description": "Helper prompt",
        "messages": [
            {"role": "user", "content": {"type": "text", "text": "What is the answer?"}},
            {"role": "assistant", "content": [{"type": "text", "text": "The answer is 42."}]},
        ],
    }
    rendered = format_prompt_result(prompt)
    assert "# Helper prompt" in rendered
    assert "[USER]" in rendered
    assert "What is the answer?" in rendered
    assert "[ASSISTANT]" in rendered
    assert "The answer is 42." in rendered

    # Empty prompt
    assert format_prompt_result({"messages": []}) == "(empty prompt)"

    # Non-mapping
    assert format_prompt_result("raw") == '"raw"'


def test_mcp_client_resources_and_prompts_discovery(tmp_path: Path):
    script = tmp_path / "caps_server.py"
    script.write_text(FAKE_CAPABILITIES_SERVER, encoding="utf-8")

    cfg_file = tmp_path / "mcp.json"
    cfg_file.write_text(
        json.dumps({
            "mcpServers": {
                "caps": {
                    "command": sys.executable,
                    "args": [str(script)],
                }
            }
        }),
        encoding="utf-8",
    )

    session = MCPSession()
    try:
        statuses = session.start(load_config(cfg_file))
        assert len(statuses) == 1
        st = statuses[0]
        assert st.ok is True
        assert st.tools == 1
        assert st.prompts == 1
        assert st.resources == 1

        # Check registered tool specs
        specs = {s.name: s for s in all_specs()}
        assert "mcp_caps_ping" in specs
        assert "mcp_caps_read_resource" in specs
        assert "mcp_caps_list_resources" in specs
        assert "mcp_caps_get_prompt" in specs
        assert "mcp_caps_list_prompts" in specs

        # Call read_resource tool spec
        read_res_spec = specs["mcp_caps_read_resource"]
        content = read_res_spec.handler({"uri": "memo://guidelines"}, None)
        assert "# Repository Guidelines" in content
        assert "Clean code only." in content

        # Call read_resource with invalid URI
        err_content = read_res_spec.handler({"uri": "memo://missing"}, None)
        assert "failed" in err_content.lower()

        # Call list_resources tool spec
        list_res_spec = specs["mcp_caps_list_resources"]
        listed_str = list_res_spec.handler({}, None)
        assert "memo://guidelines" in listed_str

        # Call get_prompt tool spec
        get_prompt_spec = specs["mcp_caps_get_prompt"]
        prompt_content = get_prompt_spec.handler({"name": "review-code"}, None)
        assert "Review code against guidelines" in prompt_content
        assert "Please review the specified file" in prompt_content

        # Call list_prompts tool spec
        list_prompts_spec = specs["mcp_caps_list_prompts"]
        listed_p_str = list_prompts_spec.handler({}, None)
        assert "review-code" in listed_p_str

        # Programmatic methods on MCPSession
        res_list = session.list_resources("caps")
        assert len(res_list) == 1
        assert res_list[0]["uri"] == "memo://guidelines"

        direct_read = session.read_resource("caps", "memo://guidelines")
        assert "Clean code only." in direct_read

        p_list = session.list_prompts("caps")
        assert len(p_list) == 1
        assert p_list[0]["name"] == "review-code"

        direct_prompt = session.get_prompt("caps", "review-code")
        assert "Review code against guidelines" in direct_prompt

    finally:
        session.close()

    # Verify specs were completely unregistered
    specs_after = {s.name for s in all_specs()}
    assert "mcp_caps_ping" not in specs_after
    assert "mcp_caps_read_resource" not in specs_after
    assert "mcp_caps_list_resources" not in specs_after
    assert "mcp_caps_get_prompt" not in specs_after
    assert "mcp_caps_list_prompts" not in specs_after




