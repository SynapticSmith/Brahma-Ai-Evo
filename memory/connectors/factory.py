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


def get_memory_connector(backend_name: Optional[str] = None) -> BaseMemoryConnector:
    """Retrieve or create the process-wide memory backend connector."""
    selected = backend_name
    if not selected:
        try:
            from memory import config_manager
            selected = str(config_manager.get_setting("memory_backend", "local") or "local").strip().lower()
        except Exception:
            selected = "local"

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
