"""
memory/connectors/
Modular external and local memory connector architecture for Brahma AI Evo.

Provides pluggable memory backends:
- LocalMemoryConnector (default, offline, SQLite/JSON)
- Mem0MemoryConnector (Mem0 hybrid memory layer)
- LettaMemoryConnector (Letta / MemGPT tiered archival memory)
"""

from __future__ import annotations

from .base import BaseMemoryConnector, MemoryEntry
from .factory import (
    get_active_backend_name,
    get_memory_connector,
    list_available_backends,
    set_active_connector,
)
from .local import LocalMemoryConnector
from .mem0 import Mem0MemoryConnector
from .letta import LettaMemoryConnector

__all__ = [
    "BaseMemoryConnector",
    "MemoryEntry",
    "LocalMemoryConnector",
    "Mem0MemoryConnector",
    "LettaMemoryConnector",
    "get_active_backend_name",
    "get_memory_connector",
    "list_available_backends",
    "set_active_connector",
]
