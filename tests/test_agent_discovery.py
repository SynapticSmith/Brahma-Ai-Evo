"""Unit tests for agent discovery detectors (ADR-018, Q2)."""

import json

from core.agent_bridge.detectors import (
    DetectionContext,
    detect_cli_agents,
    detect_local_model_servers,
    detect_vendor_configs,
    run_all,
)


def test_detect_cli_agents_with_mock_which():
    # Mock which function that pretends claude and aider are installed
    installed = {
        "claude.cmd": "C:\\Program Files\\Claude\\claude.cmd",
        "aider.exe": "C:\\Python\\Scripts\\aider.exe",
    }

    ctx = DetectionContext(which_fn=lambda name: installed.get(name))
    records = detect_cli_agents(ctx)

    found_ids = {r.id for r in records}
    assert "claude-code" in found_ids
    assert "aider" in found_ids
    assert "codex-cli" not in found_ids

    claude_rec = next(r for r in records if r.id == "claude-code")
    assert claude_rec.executable == "C:\\Program Files\\Claude\\claude.cmd"
    assert claude_rec.sources == ["path"]
    assert claude_rec.transport.mode == "stdio"


def test_detect_local_model_servers_with_mock_ports():
    # Pretend port 11434 (Ollama) is open, 1234 (LM Studio) is closed
    open_ports = {("127.0.0.1", 11434)}

    def mock_check_port(host: str, port: int, timeout: float = 0.25) -> bool:
        return (host, port) in open_ports

    ctx = DetectionContext(port_check_fn=mock_check_port)
    records = detect_local_model_servers(ctx)

    assert len(records) == 1
    ollama = records[0]
    assert ollama.id == "ollama"
    assert ollama.kind == "local_model"
    assert ollama.endpoint == "http://127.0.0.1:11434/v1"
    assert ollama.sources == ["local_port"]


def test_detect_vendor_configs_finds_cursor_and_claude_mcp(tmp_path):
    fake_home = tmp_path / "home"
    fake_appdata = tmp_path / "appdata"
    fake_home.mkdir()
    fake_appdata.mkdir()

    # 1. Create a fake Cursor MCP config
    cursor_dir = fake_home / ".cursor"
    cursor_dir.mkdir()
    cursor_mcp = cursor_dir / "mcp.json"
    cursor_mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\projects"],
                        "env": {"DEBUG": "1"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    # 2. Create a fake Claude Desktop MCP config
    claude_dir = fake_appdata / "Claude"
    claude_dir.mkdir()
    claude_mcp = claude_dir / "claude_desktop_config.json"
    claude_mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "command": "docker",
                        "args": ["run", "-i", "mcp/github"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    ctx = DetectionContext(
        home_dir=fake_home,
        env={"APPDATA": str(fake_appdata)},
    )

    records = detect_vendor_configs(ctx)
    ids = {r.id for r in records}
    assert "mcp_filesystem" in ids
    assert "mcp_github" in ids

    fs_rec = next(r for r in records if r.id == "mcp_filesystem")
    assert fs_rec.kind == "mcp_server"
    assert fs_rec.executable == "npx"
    assert fs_rec.transport.mode == "mcp_stdio"
    assert fs_rec.sources == ["vendor_config"]


def test_run_all_unions_evidence_and_deduplicates():
    installed = {"claude.exe": "C:\\bin\\claude.exe"}
    open_ports = {("127.0.0.1", 11434)}

    ctx = DetectionContext(
        which_fn=lambda name: installed.get(name),
        port_check_fn=lambda h, p, t=0.25: (h, p) in open_ports,
    )

    records = run_all(ctx)
    ids = [r.id for r in records]
    assert "claude-code" in ids
    assert "ollama" in ids

    # Result is deterministically sorted
    assert ids == sorted(ids)


def test_run_all_never_raises_on_faulty_detector(monkeypatch):
    ctx = DetectionContext(
        which_fn=lambda _: 1 / 0,  # deliberately raises ZeroDivisionError
    )

    # Must return cleanly (empty list or whatever didn't fail)
    records = run_all(ctx)
    assert isinstance(records, list)
