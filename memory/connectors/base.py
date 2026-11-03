"""
memory/connectors/base.py
Abstract base connector specification for Brahma AI Evo memory backends.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class MemoryEntry:
    """Canonical memory representation across all backends."""

    key: str
    value: str
    category: str = "notes"
    score: float = 1.0
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "category": self.category,
            "score": self.score,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }


class BaseMemoryConnector(ABC):
    """Abstract interface that every Brahma memory backend implements."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Unique identifier of the memory backend (e.g. 'local', 'mem0', 'letta')."""

    @abstractmethod
    def store(
        self,
        key: str,
        value: Any,
        category: str = "notes",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Store or update a memory entry."""

    @abstractmethod
    def recall(self, query: str, limit: int = 8) -> List[MemoryEntry]:
        """Search and recall memory entries matching a query."""

    @abstractmethod
    def delete(self, key: str, category: Optional[str] = None) -> bool:
        """Delete a memory entry by key."""

    @abstractmethod
    def list_all(self, category: Optional[str] = None) -> List[MemoryEntry]:
        """List stored memory entries."""

    @abstractmethod
    def status(self) -> Dict[str, Any]:
        """Check liveness and diagnostic health of the memory backend."""
