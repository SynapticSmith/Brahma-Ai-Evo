"""
memory/connectors/factory.py
Factory for creating and resolving configured memory backend connectors.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .base import BaseMemoryConnector
from .local import LocalMemoryConnector
from .mem0 import Mem0MemoryConnector
from .letta import LettaMemoryConnector

logger = logging.getLogger("MemoryConnectorFactory")

_INSTANCES: Dict[str, BaseMemoryConnector] = {}
_active_backend: Optional[str] = None


def list_available_backends() -> List[Dict[str, str]]:
    """Enumerate the supported memory backends with friendly descriptions."""
    return [
        {
            "id": "local",
            "name": "Local Storage (Built-in)",
            "description": "Zero-dependency offline SQLite and JSON memory. Always available.",
        },
        {
            "id": "mem0",
            "name": "Mem0 Hybrid Memory",
            "description": "Adaptive vector & graph memory layer with local Qdrant or cloud sync.",
        },
        {
            "id": "letta",
            "name": "Letta / MemGPT Tiered Memory",
            "description": "Stateful agent archival and recall memory over REST API.",
        },
    ]


def get_active_backend_name() -> str:
    """Retrieve the currently active memory backend name."""
    global _active_backend
    if _active_backend:
        return _active_backend
    try:
        from memory import config_manager

        val = str(config_manager.get_setting("memory_backend", "local") or "local").strip().lower()
        if val in ("local", "mem0", "letta"):
            _active_backend = val
            return val
    except Exception:
        pass
    _active_backend = "local"
    return "local"


def set_active_connector(backend_name: str) -> BaseMemoryConnector:
    """Set and persist the active memory backend connector."""
    global _active_backend
    name = backend_name.strip().lower()
    if name not in ("local", "mem0", "letta"):
        name = "local"
    _active_backend = name
    try:
        from memory import config_manager

        config_manager.set_setting("memory_backend", name)
    except Exception:
        pass
    return get_memory_connector(name)


def get_memory_connector(backend_name: Optional[str] = None) -> BaseMemoryConnector:
    """Retrieve or create the process-wide memory backend connector."""
    selected = backend_name or get_active_backend_name()
    if selected not in ("local", "mem0", "letta"):
        selected = "local"

    if selected in _INSTANCES:
        return _INSTANCES[selected]

    if selected == "mem0":
        connector = Mem0MemoryConnector()
    elif selected == "letta":
        connector = LettaMemoryConnector()
    else:
        connector = LocalMemoryConnector()

    _INSTANCES[selected] = connector
    return connector


def reset_connectors() -> None:
    """Clear cached connector instances so configuration changes take effect immediately."""
    global _INSTANCES, _active_backend
    _INSTANCES.clear()
    _active_backend = None
