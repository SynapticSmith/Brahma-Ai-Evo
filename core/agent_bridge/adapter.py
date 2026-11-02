"""AgentAdapter abstract base class and ToolSpec publication (ADR-018, Q2)."""

from __future__ import annotations

from abc import ABC, abstractmethod
import re
from typing import Any, ClassVar, Dict, List, Optional

from agent.spec import ToolContext, ToolSpec
from core.agent_bridge.constants import TOOL_NAME_TEMPLATE
from core.agent_bridge.detectors import DetectionContext
from core.agent_bridge.models import (
    AgentCallResult,
    AgentRecord,
    Capability,
    ProbeResult,
)


def sanitize_tool_name(name: str) -> str:
    """Sanitize name to valid ToolSpec characters: [a-z0-9_]."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", name.lower()).strip("_")
    return cleaned or "tool"


class AgentAdapter(ABC):
    """Uniform adapter interface for discovered agents and external tool sources."""

    KIND: ClassVar[str] = "agent"
    TIER: ClassVar[int] = 1
    AGENT_ID: ClassVar[str] = ""

    def __init__(self, record: AgentRecord):
        self.record = record

    @classmethod
    @abstractmethod
    def detect(cls, ctx: DetectionContext) -> Optional[AgentRecord]:
        """Pure detection check. Returns AgentRecord if found, else None."""

    @abstractmethod
    def probe(self) -> ProbeResult:
        """Perform multi-stage diagnostic probe (liveness, protocol, capabilities)."""

    @abstractmethod
    def connect(self) -> None:
        """Establish transport connection and load capabilities."""

    @abstractmethod
    def verbs(self) -> List[Capability]:
        """Return list of capabilities available on this agent."""

    @abstractmethod
    def call(self, verb: str, args: Dict[str, Any]) -> AgentCallResult:
        """Invoke a verb on the agent. Never raises for agent faults."""

    @abstractmethod
    def close(self) -> None:
        """Terminate connection and kill child process trees."""

    def to_tool_specs(self) -> List[ToolSpec]:
        """Export agent capabilities as canonical ToolSpecs behind AgentRuntime (ADR-018)."""
        specs: List[ToolSpec] = []
        for cap in self.verbs():
            raw_name = TOOL_NAME_TEMPLATE.format(id=self.record.id, verb=cap.name)
            tool_name = sanitize_tool_name(raw_name)

            # Determine appropriate tool_class
            if self.record.kind == "mcp_server":
                tool_class = "mcp"
            elif self.record.kind == "cli_agent":
                tool_class = "exec"
            elif self.record.kind == "local_model":
                tool_class = "network"
            else:
                tool_class = "write" if cap.mutating else "read"

            def _make_handler(verb_name: str):
                def _handler(args: dict, context: ToolContext) -> str:
                    result = self.call(verb_name, args)
                    if not result.ok:
                        return f"Error executing {verb_name}: {result.error or result.output}"
                    return result.output
                return _handler

            spec = ToolSpec(
                name=tool_name,
                description=f"[AGENT:{self.record.display_name}] {cap.description}",
                parameters=cap.parameters or {"type": "object", "properties": {}},
                tool_class=tool_class,
                handler=_make_handler(cap.name),
                requires_approval=cap.mutating,
            )
            specs.append(spec)
        return specs
