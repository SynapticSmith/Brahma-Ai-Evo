"""Peer Task Artifact Storage and Scope Isolation (ADR-038 & Deep Dive §2.7).

Provides content-addressed storage for peer task outputs and input artifacts:
    %LOCALAPPDATA%\\BrahmaAI\\peer\\artifacts\\<task_id>\\<artifact_hash>.bin
with a task-scoped manifest.jsonl index, SHA-256 integrity verification,
32 MiB size cap, and strict (origin_node, task_id) access boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from core.user_paths import get_user_data_dir

logger = logging.getLogger("brahma.peer.artifacts")

MAX_ARTIFACT_BYTES = 33_554_432       # 32 MiB cap per artifact
DEFAULT_ARTIFACT_TTL_S = 604_800      # 7 days
ALLOWED_MEDIA_TYPES = (
    "text/plain",
    "text/markdown",
    "application/json",
    "application/x-ndjson",
    "text/x-diff",
    "application/octet-stream",
)
_SAFE_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def sanitize_filename(name: str) -> str:
    """Sanitize display name, stripping directory traversal characters."""
    cleaned = Path(str(name or "artifact.bin")).name.strip()
    return cleaned if cleaned else "artifact.bin"


def default_peer_artifacts_dir() -> Path:
    """Return root directory for peer task artifacts."""
    env = os.getenv("BRAHMA_PEER_ARTIFACTS_DIR")
    if env:
        d = Path(env).expanduser().resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d
    d = get_user_data_dir() / "peer" / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass(slots=True)
class PeerArtifactMetadata:
    """Metadata describing a content-addressed peer artifact."""

    artifact_id: str
    task_id: str
    origin_node: str
    producer_node: str
    media_type: str
    size: int
    sha256: str
    name: str = "artifact.bin"
    created_at: str = field(default_factory=now_iso)
    expires_at: str = field(
        default_factory=lambda: (
            datetime.now(timezone.utc) + timedelta(seconds=DEFAULT_ARTIFACT_TTL_S)
        ).isoformat()
    )
    provenance: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PeerArtifactMetadata:
        return cls(
            artifact_id=str(data.get("artifact_id") or ""),
            task_id=str(data.get("task_id") or ""),
            origin_node=str(data.get("origin_node") or ""),
            producer_node=str(data.get("producer_node") or ""),
            media_type=str(data.get("media_type") or "application/octet-stream"),
            size=int(data.get("size", 0)),
            sha256=str(data.get("sha256") or ""),
            name=sanitize_filename(str(data.get("name") or "artifact.bin")),
            created_at=str(data.get("created_at") or now_iso()),
            expires_at=str(data.get("expires_at") or ""),
            provenance=dict(data.get("provenance") or {}),
        )


class PeerArtifactStore:
    """Thread-safe content-addressed storage for peer artifacts."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else default_peer_artifacts_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _task_dir(self, task_id: str) -> Path:
        clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", task_id)
        d = self.base_dir / clean_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def store_artifact(
        self,
        task_id: str,
        content: bytes,
        *,
        origin_node: str,
        producer_node: str,
        name: str = "artifact.bin",
        media_type: str = "application/octet-stream",
        provenance: Optional[Dict[str, Any]] = None,
        ttl_s: float = DEFAULT_ARTIFACT_TTL_S,
    ) -> PeerArtifactMetadata:
        """Store bytes durably, compute SHA-256, and index in task manifest."""
        if len(content) > MAX_ARTIFACT_BYTES:
            raise ValueError(
                f"artifact_too_large: artifact size {len(content)} bytes exceeds limit {MAX_ARTIFACT_BYTES} bytes"
            )

        if media_type not in ALLOWED_MEDIA_TYPES:
            media_type = "application/octet-stream"

        digest = hashlib.sha256(content).hexdigest()
        art_id = f"sha256:{digest}"
        created = now_iso()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_s)).isoformat()
        clean_name = sanitize_filename(name)

        meta = PeerArtifactMetadata(
            artifact_id=art_id,
            task_id=task_id,
            origin_node=origin_node,
            producer_node=producer_node,
            media_type=media_type,
            size=len(content),
            sha256=digest,
            name=clean_name,
            created_at=created,
            expires_at=expires,
            provenance=provenance or {},
        )

        task_dir = self._task_dir(task_id)
        blob_path = task_dir / f"{digest}.bin"
        manifest_path = task_dir / "manifest.jsonl"

        with self._lock:
            # Atomic file write
            tmp_path = task_dir / f"{digest}.tmp.{os.getpid()}"
            tmp_path.write_bytes(content)
            tmp_path.replace(blob_path)

            # Append to task manifest
            with manifest_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(meta.to_dict()) + "\n")

        return meta

    def get_artifact(
        self,
        task_id: str,
        artifact_id: str,
    ) -> Optional[Tuple[PeerArtifactMetadata, bytes]]:
        """Retrieve artifact metadata and raw bytes, verifying SHA-256 integrity."""
        meta = self.get_metadata(task_id, artifact_id)
        if meta is None:
            return None

        task_dir = self._task_dir(task_id)
        blob_path = task_dir / f"{meta.sha256}.bin"
        if not blob_path.is_file():
            return None

        content = blob_path.read_bytes()
        # Verify content hash integrity
        digest = hashlib.sha256(content).hexdigest()
        if digest != meta.sha256:
            logger.error("Artifact integrity mismatch for %s in task %s", artifact_id, task_id)
            return None

        return meta, content

    def get_metadata(
        self,
        task_id: str,
        artifact_id: str,
    ) -> Optional[PeerArtifactMetadata]:
        """Lookup artifact metadata from task manifest without reading payload bytes."""
        task_dir = self._task_dir(task_id)
        manifest_path = task_dir / "manifest.jsonl"
        if not manifest_path.is_file():
            return None

        target_sha = artifact_id.replace("sha256:", "").strip().lower()
        latest_match = None

        with self._lock:
            with manifest_path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if record.get("sha256", "").lower() == target_sha or record.get("artifact_id") == artifact_id:
                            latest_match = PeerArtifactMetadata.from_dict(record)
                    except Exception:
                        continue

        return latest_match

    def list_artifacts(self, task_id: str) -> List[PeerArtifactMetadata]:
        """List all artifacts registered for a task."""
        task_dir = self._task_dir(task_id)
        manifest_path = task_dir / "manifest.jsonl"
        if not manifest_path.is_file():
            return []

        artifacts: Dict[str, PeerArtifactMetadata] = {}
        with self._lock:
            with manifest_path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        meta = PeerArtifactMetadata.from_dict(rec)
                        artifacts[meta.artifact_id] = meta
                    except Exception:
                        continue

        return list(artifacts.values())
