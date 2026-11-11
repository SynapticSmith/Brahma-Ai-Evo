"""Tests for Peer Task Artifact storage, scope isolation, and HTTP endpoints (ADR-038 & §2.7)."""

from __future__ import annotations

import hashlib
from pathlib import Path
import time
import pytest

from api_support import auth, build_client, make_config, mint
from core.peer.artifacts import MAX_ARTIFACT_BYTES, PeerArtifactStore
from core.peer.model import BrahmaTaskRequest
from core.peer.store import PeerTaskStore


def test_peer_artifact_store_crud_and_integrity(tmp_path: Path):
    """Verify content-addressed storage, SHA-256 verification, and manifest indexing."""
    store = PeerArtifactStore(tmp_path / "artifacts")
    content = b"# Artifact Report\nFederated execution completed cleanly.\n"
    digest = hashlib.sha256(content).hexdigest()

    # 1. Store artifact
    meta = store.store_artifact(
        task_id="task-art-1",
        content=content,
        origin_node="node-a",
        producer_node="node-b",
        name="report.md",
        media_type="text/markdown",
        provenance={"turn": 3, "tool": "read_file"},
    )
    assert meta.artifact_id == f"sha256:{digest}"
    assert meta.sha256 == digest
    assert meta.size == len(content)
    assert meta.name == "report.md"
    assert meta.media_type == "text/markdown"

    # 2. Retrieve metadata
    read_meta = store.get_metadata("task-art-1", meta.artifact_id)
    assert read_meta is not None
    assert read_meta.sha256 == digest

    # 3. Retrieve raw bytes and verify integrity
    res = store.get_artifact("task-art-1", meta.artifact_id)
    assert res is not None
    loaded_meta, loaded_bytes = res
    assert loaded_bytes == content
    assert loaded_meta.artifact_id == meta.artifact_id

    # 4. Tampering detection
    blob_path = tmp_path / "artifacts" / "task-art-1" / f"{digest}.bin"
    blob_path.write_bytes(b"corrupted content")
    assert store.get_artifact("task-art-1", meta.artifact_id) is None

    # 5. List artifacts
    store.store_artifact(
        task_id="task-art-1",
        content=b'{"key": "value"}',
        origin_node="node-a",
        producer_node="node-b",
        name="data.json",
        media_type="application/json",
    )
    arts = store.list_artifacts("task-art-1")
    assert len(arts) == 2
    names = {a.name for a in arts}
    assert "report.md" in names
    assert "data.json" in names


def test_peer_artifact_size_cap(tmp_path: Path):
    """Verify that artifacts exceeding 32 MiB are rejected."""
    store = PeerArtifactStore(tmp_path / "artifacts")
    oversized = b"x" * (MAX_ARTIFACT_BYTES + 10)
    with pytest.raises(ValueError, match="artifact_too_large"):
        store.store_artifact(
            task_id="task-huge",
            content=oversized,
            origin_node="node-a",
            producer_node="node-b",
        )


def test_peer_artifact_routes_scope_isolation(tmp_path: Path):
    """Verify HTTP routes for artifact upload, download, and strict cross-origin isolation."""
    task_store = PeerTaskStore(tmp_path / "tasks.db")
    artifact_store = PeerArtifactStore(tmp_path / "artifacts")
    config = make_config(tmp_path, workspaces={"test": str(tmp_path / "ws")})
    _ra, tok_a = mint(config, "reader")
    _rb, tok_b = mint(config, "reader")
    time.sleep(1.05)

    client = build_client(
        config,
        peer_store=task_store,
        peer_artifact_store=artifact_store,
    )

    # 1. Creator A registers a task
    req_a = BrahmaTaskRequest(
        task_id="task-scope-1",
        trace_id="tr-scope-1",
        root_task_id="task-scope-1",
        origin_node="node-a",
        coordinator_node="node-a",
        workspace="test",
        prompt="Execute task with artifacts",
        profile="reader",
    )
    r_task = client.post(
        "/tasks",
        headers={**auth(tok_a), "X-Brahma-Node": "node-a"},
        json=req_a.to_dict(),
    )
    assert r_task.status_code == 202

    # 2. Creator A uploads an artifact
    artifact_data = b"confidential research findings for node-a"
    digest = hashlib.sha256(artifact_data).hexdigest()
    r_upload = client.post(
        "/tasks/task-scope-1/artifacts",
        content=artifact_data,
        headers={
            **auth(tok_a),
            "X-Brahma-Node": "node-a",
            "Content-Type": "text/plain",
            "X-Brahma-Artifact-Name": "../../traversal_report.txt",
        },
    )
    assert r_upload.status_code == 201
    meta = r_upload.json()
    assert meta["artifact_id"] == f"sha256:{digest}"
    assert meta["sha256"] == digest
    assert meta["name"] == "traversal_report.txt"  # Sanitized!

    # 3. Creator A downloads the artifact successfully
    r_fetch = client.get(
        f"/tasks/task-scope-1/artifacts/{meta['artifact_id']}",
        headers={**auth(tok_a), "X-Brahma-Node": "node-a"},
    )
    assert r_fetch.status_code == 200
    assert r_fetch.content == artifact_data
    assert r_fetch.headers["X-Brahma-Artifact-SHA256"] == digest

    # 4. Creator A lists artifacts
    r_list = client.get(
        "/tasks/task-scope-1/artifacts",
        headers={**auth(tok_a), "X-Brahma-Node": "node-a"},
    )
    assert r_list.status_code == 200
    assert len(r_list.json()["artifacts"]) == 1

    # 5. Security Check: Unrelated Token B attempting to access gets 403 Forbidden
    r_spoof_fetch = client.get(
        f"/tasks/task-scope-1/artifacts/{meta['artifact_id']}",
        headers={**auth(tok_b), "X-Brahma-Node": "node-a"},
    )
    assert r_spoof_fetch.status_code == 403
    assert r_spoof_fetch.json()["error"]["code"] == "origin_mismatch"

    r_spoof_list = client.get(
        "/tasks/task-scope-1/artifacts",
        headers={**auth(tok_b), "X-Brahma-Node": "node-a"},
    )
    assert r_spoof_list.status_code == 403

    # 6. Non-existent artifact returns 404
    r_not_found = client.get(
        "/tasks/task-scope-1/artifacts/sha256:0000000000000000000000000000000000000000000000000000000000000000",
        headers={**auth(tok_a), "X-Brahma-Node": "node-a"},
    )
    assert r_not_found.status_code == 404
    assert r_not_found.json()["error"]["code"] == "artifact_not_found"
