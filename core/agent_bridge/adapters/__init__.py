"""Adapter implementations for external agent families."""

from __future__ import annotations

from core.agent_bridge.adapters.local_model import LocalModelAdapter
from core.agent_bridge.adapters.mcp_server import MCPServerAdapter

__all__ = ["LocalModelAdapter", "MCPServerAdapter"]
