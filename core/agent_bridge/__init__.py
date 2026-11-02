"""Agent & Orchestrator Bridge (ADR-018, Q2).

A tool source that discovers local and external agents, probes capabilities,
and publishes them as canonical ToolSpecs behind AgentRuntime.execute.
"""

from __future__ import annotations

from core.agent_bridge.adapter import AgentAdapter, sanitize_tool_name
from core.agent_bridge.adapters.cli_agent import CLIAgentAdapter
from core.agent_bridge.adapters.local_model import LocalModelAdapter
from core.agent_bridge.adapters.mcp_server import MCPServerAdapter
from core.agent_bridge.artifacts import (
    clean_expired_artifacts,
    create_read_artifact_spec,
    list_artifacts,
    persist_call_result,
    read_artifact,
    save_artifact,
)
from core.agent_bridge.bridge import AgentBridge, close_agent_bridge, get_agent_bridge
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
    "AgentBridge",
    "AgentBridgeError",
    "AgentCallResult",
    "AgentRecord",
    "AgentRegistry",
    "AgentTransport",
    "AuthRequired",
    "CallTimeout",
    "Capability",
    "CLIAgentAdapter",
    "clean_expired_artifacts",
    "close_agent_bridge",
    "ConfirmationDenied",
    "ConnectError",
    "create_read_artifact_spec",
    "default_registry_path",
    "DetectionContext",
    "FanoutBudgetExceeded",
    "ForbiddenTarget",
    "get_agent_bridge",
    "list_artifacts",
    "LocalModelAdapter",
    "MCPServerAdapter",
    "persist_call_result",
    "ProbeResult",
    "read_artifact",
    "run_all",
    "sanitize_tool_name",
    "save_artifact",
    "SchemaDrift",
]
