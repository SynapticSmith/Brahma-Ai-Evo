"""Constants and configuration for the Agent & Orchestrator Bridge (ADR-018)."""

from __future__ import annotations

from typing import Any, Dict, List

SOURCE_PREFIX_AGENT = "agent"
SOURCE_PREFIX_ORCH = "agent:orchestrator"
TOOL_NAME_TEMPLATE = "agent_{id}_{verb}"

# Timeouts (seconds)
DEFAULT_CALL_TIMEOUT_SECONDS = 120.0
PROBE_TIMEOUT_SECONDS = 3.0
DISCOVERY_WARM_BUDGET_SECONDS = 1.5

# Registry storage filename
AGENT_REGISTRY_FILENAME = "agent_registry.json"

# Output durability & in-context limits (ADR-015)
MAX_AGENT_OUTPUT_BYTES = 256 * 1024
ARTIFACTS_ROOT = "agent_artifacts"
ARTIFACT_REF_PREFIX = "agent-out://"

# Known local model server ports for Layer C (loopback probing only)
KNOWN_LOCAL_SERVERS: List[Dict[str, Any]] = [
    {
        "id": "ollama",
        "display_name": "Ollama Model Server",
        "kind": "local_model",
        "tier": 1,
        "host": "127.0.0.1",
        "port": 11434,
        "endpoint": "http://127.0.0.1:11434/v1",
        "transport_mode": "http_openai",
    },
    {
        "id": "lm_studio",
        "display_name": "LM Studio Local Server",
        "kind": "local_model",
        "tier": 1,
        "host": "127.0.0.1",
        "port": 1234,
        "endpoint": "http://127.0.0.1:1234/v1",
        "transport_mode": "http_openai",
    },
    {
        "id": "vllm",
        "display_name": "vLLM Inference Server",
        "kind": "local_model",
        "tier": 1,
        "host": "127.0.0.1",
        "port": 8000,
        "endpoint": "http://127.0.0.1:8000/v1",
        "transport_mode": "http_openai",
    },
]

# Known CLI coding agents for Layer A/B
KNOWN_CLI_AGENTS: List[Dict[str, Any]] = [
    {
        "id": "claude-code",
        "display_name": "Claude Code",
        "kind": "cli_agent",
        "tier": 1,
        "binaries": ["claude.cmd", "claude.exe", "claude"],
        "default_argv": ["claude", "--print", "--output-format", "json"],
    },
    {
        "id": "codex-cli",
        "display_name": "Codex CLI",
        "kind": "cli_agent",
        "tier": 1,
        "binaries": ["codex.cmd", "codex.exe", "codex"],
        "default_argv": ["codex", "exec"],
    },
    {
        "id": "aider",
        "display_name": "Aider Coding Agent",
        "kind": "cli_agent",
        "tier": 1,
        "binaries": ["aider.exe", "aider"],
        "default_argv": ["aider", "--yes", "--no-git"],
    },
    {
        "id": "gemini-cli",
        "display_name": "Gemini CLI Agent",
        "kind": "cli_agent",
        "tier": 1,
        "binaries": ["gemini.cmd", "gemini.exe", "gemini"],
        "default_argv": ["gemini", "run"],
    },
]
