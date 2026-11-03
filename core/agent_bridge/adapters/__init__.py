"""Adapter implementations for external agent families."""

from __future__ import annotations

from core.agent_bridge.adapters.cli_agent import CLIAgentAdapter
from core.agent_bridge.adapters.local_model import LocalModelAdapter
from core.agent_bridge.adapters.mcp_server import MCPServerAdapter
from core.agent_bridge.adapters.orchestrator import OrchestratorAdapter
from core.agent_bridge.adapters.remote import RemoteAgentAdapter

__all__ = [
    "CLIAgentAdapter",
    "LocalModelAdapter",
    "MCPServerAdapter",
    "OrchestratorAdapter",
    "RemoteAgentAdapter",
]
