"""Error taxonomy for the Agent & Orchestrator Bridge."""

from __future__ import annotations


class AgentBridgeError(Exception):
    """Base exception for all agent bridge operations."""


class ConnectError(AgentBridgeError):
    """Transport or protocol failure during connection."""


class AuthRequired(AgentBridgeError):
    """Authentication required to communicate with agent."""


class CallTimeout(AgentBridgeError):
    """Execution time limit exceeded."""


class SchemaDrift(AgentBridgeError):
    """Expected schema does not match agent response schema."""


class ForbiddenTarget(AgentBridgeError):
    """Target identity is forbidden (e.g. self-target/recursion loop)."""


class FanoutBudgetExceeded(AgentBridgeError):
    """Concurrency or spend cap reached."""


class ConfirmationDenied(AgentBridgeError):
    """User or policy denied confirmation for mutating action."""
