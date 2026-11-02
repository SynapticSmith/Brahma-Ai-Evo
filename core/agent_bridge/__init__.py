"""Agent & Orchestrator Bridge (ADR-018, Q2).

A tool source that discovers local and external agents, probes capabilities,
and publishes them as canonical ToolSpecs behind AgentRuntime.execute.
"""

from __future__ import annotations

from core.agent_bridge.adapter import AgentAdapter, sanitize_tool_name
from core.agent_bridge.adapters.local_model import LocalModelAdapter
from core.agent_bridge.adapters.mcp_server import MCPServerAdapter
from core.agent_bridge.detectors import DetectionContext, run_all
from core.agent_bridge.errors import (
    AgentBridgeError,
    AuthRequired,
    CallTimeout,
    ConfirmationDenied,
    ConnectError,
    FanoutBudgetExceeded,
    ForbiddenTarget,
    SchemaDrift,
)
from core.agent_bridge.models import (
    AgentCallResult,
    AgentRecord,
    AgentTransport,
    Capability,
    ProbeResult,
)
from core.agent_bridge.registry import AgentRegistry, default_registry_path

__all__ = [
    "AgentAdapter",
    "AgentBridgeError",
    "AgentCallResult",
    "AgentRecord",
    "AgentRegistry",
    "AgentTransport",
    "AuthRequired",
    "CallTimeout",
    "Capability",
    "ConfirmationDenied",
    "ConnectError",
    "DetectionContext",
    "FanoutBudgetExceeded",
    "ForbiddenTarget",
    "LocalModelAdapter",
    "MCPServerAdapter",
    "ProbeResult",
    "SchemaDrift",
    "default_registry_path",
    "run_all",
    "sanitize_tool_name",
]
