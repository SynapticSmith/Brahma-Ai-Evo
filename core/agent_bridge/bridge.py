"""AgentBridge facade: lifecycle, discovery, and ToolSpec publishing (ADR-018, Q2).

The unified orchestrator facade managing discovery, multi-stage diagnostic probing,
adapter instantiations, and publishing external agents as canonical ToolSpecs
behind AgentRuntime.execute.
"""

from __future__ import annotations

import atexit
import logging
from pathlib import Path
import threading
from typing import Dict, List, Optional

from agent.spec import register as register_tool, unregister as unregister_tool
from core.agent_bridge.adapter import AgentAdapter
from core.agent_bridge.adapters.cli_agent import CLIAgentAdapter
from core.agent_bridge.adapters.local_model import LocalModelAdapter
from core.agent_bridge.adapters.mcp_server import MCPServerAdapter
from core.agent_bridge.artifacts import create_read_artifact_spec
from core.agent_bridge.detectors import DetectionContext, run_all
from core.agent_bridge.models import AgentRecord, ProbeResult
from core.agent_bridge.registry import AgentRegistry

logger = logging.getLogger(__name__)


class AgentBridge:
    """Central facade for agent discovery, adapter management, and tool publication."""

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        artifacts_dir: Optional[Path] = None,
    ):
        self.registry = registry if registry is not None else AgentRegistry()
        self.artifacts_dir = artifacts_dir
        self._adapters: Dict[str, AgentAdapter] = {}
        self._published_tool_names: set[str] = set()
        self._lock = threading.RLock()

    def _create_adapter(self, record: AgentRecord) -> Optional[AgentAdapter]:
        """Factory for instantiating the appropriate adapter by record kind."""
        try:
            if record.kind == "mcp_server":
                return MCPServerAdapter(record)
            elif record.kind == "local_model":
                return LocalModelAdapter(record)
            elif record.kind == "cli_agent":
                return CLIAgentAdapter(record)
            elif record.kind == "orchestrator":
                from core.agent_bridge.adapters.orchestrator import OrchestratorAdapter
                return OrchestratorAdapter(record)
            elif record.kind in ("remote_http", "remote_agent", "peer"):
                from core.agent_bridge.adapters.remote import RemoteAgentAdapter
                return RemoteAgentAdapter(record)
            else:
                logger.info("Unsupported agent kind %r for agent %s", record.kind, record.id)
                return None
        except Exception as exc:
            logger.warning("Failed to construct adapter for %s (%s): %s", record.id, record.kind, exc)
            return None

    def discover(self, workspace: Optional[Path] = None, *, force: bool = False) -> List[AgentRecord]:
        """Run all discovery layers and update active adapters."""
        with self._lock:
            search_roots = [workspace] if workspace is not None else None
            ctx = DetectionContext(search_roots=search_roots)
            detected = run_all(ctx)
            merged = self.registry.rescan(detected)

            # Sync active adapters
            current_ids = set()
            for rec in merged:
                current_ids.add(rec.id)
                if not rec.enabled:
                    # If previously active, close and remove
                    if rec.id in self._adapters:
                        try:
                            self._adapters[rec.id].close()
                        except Exception:
                            pass
                        del self._adapters[rec.id]
                    continue

                if rec.id not in self._adapters:
                    adapter = self._create_adapter(rec)
                    if adapter is not None:
                        self._adapters[rec.id] = adapter

            # Remove adapters for records that were pruned
            for removed_id in list(self._adapters.keys()):
                if removed_id not in current_ids:
                    try:
                        self._adapters[removed_id].close()
                    except Exception:
                        pass
                    del self._adapters[removed_id]

            return merged

    def probe_all(self) -> Dict[str, ProbeResult]:
        """Run diagnostic probes on all active adapters."""
        with self._lock:
            results: Dict[str, ProbeResult] = {}
            for agent_id, adapter in list(self._adapters.items()):
                try:
                    res = adapter.probe()
                    results[agent_id] = res
                    # Update status in record & registry
                    adapter.record.status = "connected" if res.ok else "degraded"
                    self.registry.save(adapter.record)
                except Exception as exc:
                    err_res = ProbeResult(
                        ok=False,
                        stage="liveness",
                        detail=f"Probe failed with exception: {exc}",
                    )
                    results[agent_id] = err_res
                    adapter.record.status = "error"
                    self.registry.save(adapter.record)
            return results

    def probe(self, agent_id: str) -> Optional[ProbeResult]:
        """Run diagnostic probe on a specific agent."""
        with self._lock:
            adapter = self._adapters.get(agent_id)
            if adapter is None:
                return None
            try:
                res = adapter.probe()
                adapter.record.status = "connected" if res.ok else "degraded"
                self.registry.save(adapter.record)
                return res
            except Exception as exc:
                logger.warning("Probe exception on adapter %s: %s", agent_id, exc)
                err_res = ProbeResult(
                    ok=False,
                    stage="liveness",
                    detail=f"Probe failed with exception: {exc}",
                )
                adapter.record.status = "error"
                self.registry.save(adapter.record)
                return err_res

    def publish_tools(self) -> List[str]:
        """Publish canonical ToolSpecs from all active adapters into agent.spec.
        
        Also publishes the canonical read_agent_artifact ToolSpec (ADR-015).
        Returns the list of registered tool names.
        """
        with self._lock:
            # 1. Register artifact retrieval tool
            artifact_spec = create_read_artifact_spec(self.artifacts_dir)
            register_tool(artifact_spec)
            self._published_tool_names.add(artifact_spec.name)

            # 2. Register tools from each active adapter
            for adapter in self._adapters.values():
                if not adapter.record.enabled:
                    continue
                try:
                    if adapter.record.status not in ("connected", "degraded"):
                        try:
                            adapter.connect()
                        except Exception as exc:
                            logger.info("Could not auto-connect %s: %s", adapter.record.id, exc)

                    specs = adapter.to_tool_specs()
                    for s in specs:
                        register_tool(s)
                        self._published_tool_names.add(s.name)
                except Exception as exc:
                    logger.warning("Failed to publish tools for adapter %s: %s", adapter.record.id, exc)

            return sorted(list(self._published_tool_names))

    def unpublish_tools(self) -> None:
        """Unregister all published bridge tools from agent.spec."""
        with self._lock:
            for name in list(self._published_tool_names):
                try:
                    unregister_tool(name)
                except Exception:
                    pass
            self._published_tool_names.clear()

    def get_adapter(self, agent_id: str) -> Optional[AgentAdapter]:
        with self._lock:
            return self._adapters.get(agent_id)

    def list_records(self) -> List[AgentRecord]:
        with self._lock:
            return list(self.registry.load().values())

    def close(self) -> None:
        """Unpublish all tools and close all active adapter connections."""
        with self._lock:
            self.unpublish_tools()
            for adapter in list(self._adapters.values()):
                try:
                    adapter.close()
                except Exception as exc:
                    logger.warning("Error closing adapter %s: %s", adapter.record.id, exc)
            self._adapters.clear()


# --- Process-wide global bridge singleton ---
_GLOBAL_BRIDGE: Optional[AgentBridge] = None
_bridge_lock = threading.Lock()


def get_agent_bridge(
    registry: Optional[AgentRegistry] = None,
    artifacts_dir: Optional[Path] = None,
) -> AgentBridge:
    """Access the process-wide AgentBridge instance."""
    global _GLOBAL_BRIDGE
    with _bridge_lock:
        if _GLOBAL_BRIDGE is None:
            _GLOBAL_BRIDGE = AgentBridge(registry=registry, artifacts_dir=artifacts_dir)
        return _GLOBAL_BRIDGE


def close_agent_bridge() -> None:
    """Shut down the process-wide AgentBridge if instantiated."""
    global _GLOBAL_BRIDGE
    with _bridge_lock:
        if _GLOBAL_BRIDGE is not None:
            try:
                _GLOBAL_BRIDGE.close()
            except Exception:
                pass
            _GLOBAL_BRIDGE = None


# Ensure clean shutdown on process termination
atexit.register(close_agent_bridge)
