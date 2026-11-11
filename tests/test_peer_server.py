"""Integration tests for BTE-1 peer task server (ADR-030, Invariants S-01, S-02, S-04, S-08)."""

from __future__ import annotations

import json
from pathlib import Path
import time
import pytest

from api_support import CaptureCompleter, auth, build_client, make_config, mint
from core.peer.model import BTE_VERSION, BrahmaTaskRequest
from core.peer.state import WireState
from core.peer.store import PeerTaskStore


@pytest.fixture
def peer_env(tmp_path: Path):
    db_file = tmp_path / "peer_tasks.db"
    from core.peer.store import PeerTaskStore
    store = PeerTaskStore(db_file)

    config = make_config(
        tmp_path,
        node_name="Workshop PC",
        workspaces={"test": str(tmp_path / "workspace")},
    )
    _rec, reader_token = mint(config, "reader")
    _wrec, worker_token = mint(config, "worker", confirm="worker" if "operator" == "worker" else None)
    completer = CaptureCompleter("Remote task finished successfully.")
    client = build_client(config, factory=lambda *_args, **_kwargs: completer, peer_store=store)

    return client, reader_token, worker_token, config, completer, store


def test_agent_card_returns_bte_profile(peer_env):
    client, _r_token, _w_token, _config, _completer, _store = peer_env
    resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    card = resp.json()
    assert card["schema"] == "brahma.peer/1"
    assert card["name"] == "Workshop PC"
    assert "run_task" in card["verbs"]
    assert "query_status" in card["verbs"]
    assert "cancel_task" in card["verbs"]
    assert "repo:test" in card["capabilities"]


def test_post_tasks_requires_auth(peer_env):
    client, _r_token, _w_token, _config, _completer, _store = peer_env
    task_payload = {
        "task_id": "t-unauth",
        "trace_id": "tr-unauth",
        "origin_node": "node-a",
        "coordinator_node": "node-a",
        "prompt": "Test prompt",
    }
    resp = client.post("/tasks", json=task_payload)
    assert resp.status_code == 401


def test_post_tasks_unknown_workspace(peer_env):
    client, r_token, _w_token, _config, _completer, _store = peer_env
    task_payload = {
        "task_id": "t-ws",
        "trace_id": "tr-ws",
        "origin_node": "node-a",
        "coordinator_node": "node-a",
        "prompt": "Test prompt",
        "workspace": "nonexistent_repo",
    }
    resp = client.post("/tasks", json=task_payload, headers=auth(r_token))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unknown_workspace"


def test_post_tasks_recursion_denied(peer_env):
    client, r_token, _w_token, config, _completer, _store = peer_env

    # 1. Self target
    self_node = client.get("/.well-known/agent.json").json()["node_id"].strip().lower()
    task_payload_self = {
        "task_id": "t-rec-1",
        "trace_id": "tr-rec-1",
        "origin_node": self_node,
        "coordinator_node": self_node,
        "prompt": "Loop",
        "workspace": "test",
    }
    resp = client.post("/tasks", json=task_payload_self, headers=auth(r_token))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "recursion_denied"

    # 2. Hop > max_hops (workers reject hop > 0)
    task_payload_hop = {
        "task_id": "t-rec-2",
        "trace_id": "tr-rec-2",
        "origin_node": "node-a",
        "coordinator_node": "node-a",
        "prompt": "Relay",
        "hop": 1,
    }
    resp2 = client.post("/tasks", json=task_payload_hop, headers=auth(r_token))
    assert resp2.status_code == 400
    assert resp2.json()["error"]["code"] == "recursion_denied"


def test_post_tasks_profile_ceiling_enforced(peer_env):
    client, r_token, _w_token, _config, _completer, _store = peer_env
    task_payload = {
        "task_id": "t-ceiling",
        "trace_id": "tr-ceiling",
        "origin_node": "node-a",
        "coordinator_node": "node-a",
        "prompt": "Write changes",
        "workspace": "test",
        "profile": "worker",  # reader token cannot request worker profile
    }
    resp = client.post("/tasks", json=task_payload, headers=auth(r_token))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "insufficient_profile"


def test_post_tasks_sync_execution_and_leaf_invariants(peer_env):
    client, _r_token, w_token, _config, completer, _store = peer_env
    task_id = "0192f4c1-7a2b-7c3d-9e4f-1a2b3c4d5e6f"
    task_payload = {
        "bte": BTE_VERSION,
        "task_id": task_id,
        "trace_id": "4bf9c1a27d3e4f508192a3b4c5d6e7f8",
        "origin_node": "node-a",
        "coordinator_node": "node-a",
        "prompt": "Analyze repository",
        "workspace": "test",
        "profile": "worker",
        "reply": {"mode": "sync"},
    }
    resp = client.post("/tasks", json=task_payload, headers=auth(w_token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == WireState.COMPLETED.value
    assert data["task_id"] == task_id
    assert "Remote task finished successfully." in data["result"]["text"]

    # Verify Invariant S-02: Leaf tools (subagents and peers excluded from tools list)
    assert len(completer.calls) >= 1
    sent_tools = completer.calls[0]["tools"]
    tool_names = [
        str(t.get("name") if isinstance(t, dict) else (getattr(t, "name", "") or ""))
        for t in sent_tools
    ]
    assert "run_subagent" not in tool_names
    assert "run_subagent_batch" not in tool_names
    assert "list_subagents" not in tool_names
    assert not any(n.startswith("agent_") for n in tool_names if n)


def test_post_tasks_idempotency_s04(peer_env):
    client, _r_token, w_token, _config, _completer, _store = peer_env
    task_id = "0192f4c1-idempotency-test"
    task_payload = {
        "task_id": task_id,
        "trace_id": "4bf9c1a27d3e4f508192a3b4c5d6e7f8",
        "origin_node": "node-a",
        "coordinator_node": "node-a",
        "prompt": "Inspect build",
        "workspace": "test",
        "profile": "worker",
        "reply": {"mode": "sync"},
    }
    # 1. First run
    resp1 = client.post("/tasks", json=task_payload, headers=auth(w_token))
    assert resp1.status_code == 200

    # 2. Duplicate with identical body -> returns existing task with 200
    resp2 = client.post("/tasks", json=task_payload, headers=auth(w_token))
    assert resp2.status_code == 200
    assert resp2.json()["task_id"] == task_id

    # 3. Duplicate task_id with modified prompt -> 409 Conflict
    conflict_payload = dict(task_payload)
    conflict_payload["prompt"] = "Changed prompt text!"
    resp3 = client.post("/tasks", json=conflict_payload, headers=auth(w_token))
    assert resp3.status_code == 409
    assert resp3.json()["error"]["code"] == "idempotency_conflict"


def test_get_task_status_and_cancel(peer_env):
    client, _r_token, w_token, _config, _completer, store = peer_env
    task_id = "0192f4c1-query-cancel"
    task_req = BrahmaTaskRequest(
        task_id=task_id,
        trace_id="4bf9c1a27d3e4f508192a3b4c5d6e7f8",
        root_task_id=task_id,
        origin_node="node-a",
        coordinator_node="node-a",
        prompt="Background job",
        workspace="test",
        profile="worker",
    )
    store.record_task(task_req, peer_node="node-b")

    # 1. Query status
    status_resp = client.get(
        f"/tasks/{task_id}",
        headers={**auth(w_token), "X-Brahma-Node": "node-a"},
    )
    assert status_resp.status_code == 200
    assert status_resp.json()["state"] == WireState.SUBMITTED.value

    # 2. Cancel task
    cancel_resp = client.post(
        f"/tasks/{task_id}/cancel",
        headers={**auth(w_token), "X-Brahma-Node": "node-a"},
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["state"] == WireState.CANCELED.value

    # Query status after cancel reflects canceled state
    after_cancel = client.get(
        f"/tasks/{task_id}",
        headers={**auth(w_token), "X-Brahma-Node": "node-a"},
    )
    assert after_cancel.json()["state"] == WireState.CANCELED.value


def test_a2a_agent_card_returns_v1_profile(peer_env):
    """Verify A2A v1.0 standard Agent Card endpoint."""
    client, _r_token, _w_token, _config, _completer, _store = peer_env
    resp = client.get("/.well-known/agent-card.json")
    assert resp.status_code == 200
    card = resp.json()
    assert card["protocolVersion"] == "1.0"
    assert card["name"] == "Workshop PC"
    assert card["url"].startswith("http://")
    assert card["preferredTransport"] == "HTTP+JSON"
    assert isinstance(card["skills"], list)
    assert len(card["skills"]) >= 1
    assert "repo-test" in card["skills"][0]["id"]


def test_a2a_message_send_and_colon_cancel(peer_env):
    """Verify A2A message:send execution and tasks/{id}:cancel alias."""
    client, _r_token, w_token, _config, _completer, store = peer_env

    # 1. POST /message:send
    payload = {
        "message": {
            "role": "user",
            "parts": [{"text": "Analyze the codebase"}],
        },
        "workspace": "test",
        "task_id": "a2a-task-1",
    }
    resp = client.post(
        "/message:send",
        headers={**auth(w_token), "A2A-Version": "1.0"},
        json=payload,
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["taskId"] == "a2a-task-1"
    assert res_data["state"] == "TASK_STATE_COMPLETED"
    assert "Remote task finished successfully" in res_data["response"]["parts"][0]["text"]

    # 2. Record another task and test POST /tasks/{id}:cancel
    task_req = BrahmaTaskRequest(
        task_id="a2a-task-2",
        trace_id="tr-cancel",
        root_task_id="a2a-task-2",
        origin_node="node-a",
        coordinator_node="node-a",
        prompt="Running long job",
        workspace="test",
        profile="worker",
    )
    store.record_task(task_req, peer_node="node-b")

    cancel_resp = client.post(
        "/tasks/a2a-task-2:cancel",
        headers={**auth(w_token), "X-Brahma-Node": "node-a"},
    )
    assert cancel_resp.status_code == 200
    cancel_data = cancel_resp.json()
    assert cancel_data["taskId"] == "a2a-task-2"
    assert cancel_data["state"] == "TASK_STATE_CANCELED"


def test_cross_origin_task_isolation(peer_env):
    """Security Gap 3: Cross-origin isolation prevents Token B from reading or canceling Node A's task."""
    client, reader_token, worker_token, config, _completer, store = peer_env

    # 1. Origin node-a creates a task
    task_a = BrahmaTaskRequest(
        task_id="task-isolated-1",
        trace_id="tr-iso-1",
        root_task_id="task-isolated-1",
        origin_node="node-a",
        coordinator_node="node-a",
        workspace="test",
        prompt="Origin A confidential instructions",
        profile="reader",
    )
    resp = client.post(
        "/tasks",
        headers={**auth(reader_token), "X-Brahma-Node": "node-a"},
        json=task_a.to_dict(),
    )
    assert resp.status_code == 202

    # 2. Adapter-style poll without origin header succeeds for task creator
    adapter_resp = client.get("/tasks/task-isolated-1", headers=auth(reader_token))
    assert adapter_resp.status_code == 200
    assert adapter_resp.json()["task_id"] == "task-isolated-1"

    # 3. Unrelated token attempting cross-token read via spoofed origin gets 403
    _rec, unrelated_token = mint(config, "reader")
    time.sleep(1.05)
    spoof_query_cross = client.get(
        "/tasks/task-isolated-1",
        headers={**auth(unrelated_token), "X-Brahma-Node": "node-a"},
    )
    assert spoof_query_cross.status_code == 403
    assert spoof_query_cross.json()["error"]["code"] == "origin_mismatch"

    # 4. Another origin (node-b) querying task-isolated-1 gets 404 Not Found
    spoof_query = client.get(
        "/tasks/task-isolated-1",
        headers={**auth(reader_token), "X-Brahma-Node": "node-b"},
    )
    assert spoof_query.status_code == 404
    assert spoof_query.json()["error"]["code"] == "task_not_found"

    # 5. Another origin (node-b) trying to cancel task-isolated-1 gets 404 Not Found
    spoof_cancel = client.post(
        "/tasks/task-isolated-1/cancel",
        headers={**auth(worker_token), "X-Brahma-Node": "node-b"},
    )
    assert spoof_cancel.status_code == 404

    # 6. Unrelated worker token attempting cancel via spoofed origin gets 403
    _rec, unrelated_worker = mint(config, "worker", confirm="worker")
    time.sleep(1.05)
    spoof_cancel_cross = client.post(
        "/tasks/task-isolated-1/cancel",
        headers={**auth(unrelated_worker), "X-Brahma-Node": "node-a"},
    )
    assert spoof_cancel_cross.status_code == 403
    assert spoof_cancel_cross.json()["error"]["code"] == "origin_mismatch"

    # 7. Legitimate origin (node-a) queries successfully and task is NOT canceled
    legit_query = client.get(
        "/tasks/task-isolated-1",
        headers={**auth(reader_token), "X-Brahma-Node": "node-a"},
    )
    assert legit_query.status_code == 200
    assert legit_query.json()["task_id"] == "task-isolated-1"
    assert legit_query.json()["state"] in (WireState.SUBMITTED.value, WireState.WORKING.value, WireState.COMPLETED.value)

    # 8. Bound token enforcement: token bound to node-b cannot create or claim origin node-a
    _rec, bound_token = mint(config, "reader", node_id="node-b")
    time.sleep(1.05)  # Allow TokenRegistry disk reload interval (1.0s)
    mismatch_resp = client.post(
        "/tasks",
        headers={**auth(bound_token), "X-Brahma-Node": "node-a"},
        json=task_a.to_dict(),
    )
    assert mismatch_resp.status_code == 403
    assert mismatch_resp.json()["error"]["code"] == "origin_mismatch"


def test_sync_task_failure_contract(tmp_path: Path):
    """Functional Bug 3: Failed sync tasks return HTTP 500 with error payload, not HTTP 200."""
    from core.peer.store import PeerTaskStore
    store = PeerTaskStore(tmp_path / "sync_fail.db")
    config = make_config(
        tmp_path,
        node_name="Workshop PC",
        workspaces={"test": str(tmp_path / "workspace")},
    )
    _rec, worker_token = mint(config, "worker", confirm="worker")

    def failing_factory(*_args, **_kwargs):
        def _failing_completer(*_a, **_kw):
            raise RuntimeError("Underlying model engine crashed")
        return _failing_completer

    client = build_client(config, factory=failing_factory, peer_store=store)

    task_payload = {
        "task_id": "task-sync-fail-1",
        "trace_id": "tr-sync-fail",
        "origin_node": "node-caller",
        "coordinator_node": "node-caller",
        "workspace": "test",
        "prompt": "Run doomed task",
        "profile": "worker",
        "reply": {"mode": "sync"},
    }

    resp = client.post(
        "/tasks",
        headers={**auth(worker_token), "X-Brahma-Node": "node-caller"},
        json=task_payload,
    )
    assert resp.status_code == 500
    data = resp.json()
    assert data["task_id"] == "task-sync-fail-1"
    assert data["state"] == "failed"
    assert "crashed" in data["error"]["message"]


def test_cross_token_origin_spoofing_prevented(tmp_path: Path):
    """P0 Security: Unbound token B cannot access or cancel token A's task by setting X-Brahma-Node."""
    from core.peer.store import PeerTaskStore
    store = PeerTaskStore(tmp_path / "cross_token.db")
    config = make_config(tmp_path, workspaces={"test": str(tmp_path / "workspace")})
    _rec_a, tok_a = mint(config, "reader")
    _rec_b, tok_b = mint(config, "reader")
    _rec_w, tok_w = mint(config, "worker", confirm="worker")
    time.sleep(1.05)
    client = build_client(config, peer_store=store)

    # 1. Token A creates task with origin_node="node-a"
    create_res = client.post(
        "/tasks",
        headers={**auth(tok_a), "X-Brahma-Node": "node-a"},
        json={
            "task_id": "t-cross-token",
            "trace_id": "tr-cross",
            "root_task_id": "t-cross-token",
            "origin_node": "node-a",
            "coordinator_node": "node-a",
            "workspace": "test",
            "prompt": "Confidential",
        },
    )
    assert create_res.status_code == 202

    # 2. Token B (unrelated key) claiming node-a via header is rejected with 403
    leak_read = client.get(
        "/tasks/t-cross-token",
        headers={**auth(tok_b), "X-Brahma-Node": "node-a"},
    )
    assert leak_read.status_code == 403
    assert leak_read.json()["error"]["code"] == "origin_mismatch"

    # 3. Token W (unrelated worker key) claiming node-a via header cannot cancel
    leak_cancel = client.post(
        "/tasks/t-cross-token/cancel",
        headers={**auth(tok_w), "X-Brahma-Node": "node-a"},
    )
    assert leak_cancel.status_code == 403
    assert leak_cancel.json()["error"]["code"] == "origin_mismatch"

    # 4. Token A can query its own task without X-Brahma-Node (adapter-style poll)
    owner_poll = client.get("/tasks/t-cross-token", headers=auth(tok_a))
    assert owner_poll.status_code == 200
    assert owner_poll.json()["task_id"] == "t-cross-token"


def test_peer_workspace_binding_enforced(tmp_path: Path):
    """P0 Security: POST /tasks strictly enforces principal.workspace binding matching chat lane."""
    from core.peer.store import PeerTaskStore
    store = PeerTaskStore(tmp_path / "ws_bind.db")
    config = make_config(
        tmp_path,
        workspaces={"test": str(tmp_path / "ws_test"), "vault": str(tmp_path / "ws_vault")},
    )
    (tmp_path / "ws_vault").mkdir(exist_ok=True)
    _rec, tok = mint(config, "reader", workspace="test")
    time.sleep(1.05)
    client = build_client(config, peer_store=store)

    # 1. Peer lane rejects selecting 'vault' when token is bound to 'test'
    res_peer = client.post(
        "/tasks",
        headers={**auth(tok), "X-Brahma-Node": "node-a"},
        json={
            "task_id": "t-vault-bind",
            "trace_id": "tr-vb",
            "root_task_id": "t-vault-bind",
            "origin_node": "node-a",
            "coordinator_node": "node-a",
            "workspace": "vault",
            "prompt": "Access vault files",
        },
    )
    assert res_peer.status_code == 403
    assert res_peer.json()["error"]["code"] == "insufficient_scope"
    assert "This key is bound to workspace 'test'" in res_peer.json()["error"]["message"]

    # 2. Chat lane also rejects selecting 'vault'
    res_chat = client.post(
        "/v1/chat/completions",
        headers=auth(tok),
        json={"model": "brahma-evo@vault", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert res_chat.status_code == 403
    assert res_chat.json()["error"]["code"] == "insufficient_scope"


def test_wall_clock_zero_unbounded(tmp_path: Path):
    """P1 Correctness: config.limits.wall_clock_s = 0.0 is unbounded watchdog sentinel, not 1s clamp."""
    from core.peer.store import PeerTaskStore
    store = PeerTaskStore(tmp_path / "wc_zero.db")
    config = make_config(tmp_path, workspaces={"test": str(tmp_path / "ws")})
    config.limits.wall_clock_s = 0.0

    class SlowCompleter:
        def __call__(self, messages, tools):
            time.sleep(1.5)
            return {"role": "assistant", "content": "finished without timeout"}

    client = build_client(config, factory=lambda *a, **k: SlowCompleter(), peer_store=store)
    _rec, tok = mint(config, "reader")
    time.sleep(1.05)

    res = client.post(
        "/tasks",
        headers={**auth(tok), "X-Brahma-Node": "node-a"},
        json={
            "task_id": "t-wc-zero",
            "trace_id": "tr-wcz",
            "root_task_id": "t-wc-zero",
            "origin_node": "node-a",
            "coordinator_node": "node-a",
            "workspace": "test",
            "prompt": "Test zero watchdog",
            "reply": {"mode": "sync"},
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body.get("state") == "completed"


def test_a2a_default_chat_token_execution(tmp_path: Path):
    """P1 Interop: Default chat-profile token executes on A2A /message:send and BTE /tasks."""
    from core.peer.store import PeerTaskStore
    store = PeerTaskStore(tmp_path / "a2a_chat.db")
    config = make_config(tmp_path, workspaces={"test": str(tmp_path / "workspace")})
    _rec, chat_tok = mint(config, "chat")
    time.sleep(1.05)
    client = build_client(config, peer_store=store)

    # 1. A2A /message:send with default chat-profile token
    a2a_res = client.post(
        "/message:send",
        headers=auth(chat_tok),
        json={
            "message": {"role": "user", "parts": [{"text": "Hello from A2A"}]},
            "origin_node": "a2a-client",
        },
    )
    assert a2a_res.status_code == 200
    a2a_body = a2a_res.json()
    assert a2a_body["state"] == "TASK_STATE_COMPLETED"

    # 2. BTE /tasks with default chat-profile token
    bte_res = client.post(
        "/tasks",
        headers=auth(chat_tok),
        json={
            "task_id": "t-chat-bte",
            "trace_id": "tr-cb",
            "root_task_id": "t-chat-bte",
            "origin_node": "a2a-client",
            "coordinator_node": "a2a-client",
            "workspace": "test",
            "prompt": "Quick chat query",
        },
    )
    assert bte_res.status_code in (200, 202)


def test_legacy_db_schema_migration(tmp_path: Path):
    """Verify legacy SQLite DB missing token_id and lease columns migrates without OperationalError."""
    import sqlite3
    from core.peer.store import PeerTaskStore

    legacy_db = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(legacy_db)
    conn.execute(
        """
        CREATE TABLE peer_tasks (
          task_id TEXT, origin_node TEXT, request_hash TEXT, trace_id TEXT,
          context_id TEXT, parent_task_id TEXT, root_task_id TEXT,
          coordinator_node TEXT, peer_node TEXT, state TEXT, effect TEXT,
          profile TEXT, workspace TEXT, attempt INTEGER, prompt_sha256 TEXT,
          prompt_bytes INTEGER, created_at TEXT, accepted_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO peer_tasks (task_id, origin_node, request_hash, trace_id, context_id, parent_task_id, root_task_id, coordinator_node, peer_node, state, effect, profile, workspace, attempt, prompt_sha256, prompt_bytes, created_at, accepted_at) "
        "VALUES ('legacy-1','node-old','hash','trace','ctx','parent','root','coord','peer','working','none','reader','test',1,'sha',1,'2026-01-01','2026-01-01')"
    )
    conn.commit()
    conn.close()

    # Opening with PeerTaskStore must run table migration and add columns before index creation
    store = PeerTaskStore(legacy_db)
    row = store.get_task("node-old", "legacy-1")
    assert row is not None
    assert row["task_id"] == "legacy-1"
    assert "token_id" in row
    assert "lease_expires_at" in row


def test_colliding_task_id_creator_resolution(tmp_path: Path):
    """Verify that when different origins use identical task_id, each creator's headerless poll resolves its own task."""
    from core.peer.store import PeerTaskStore
    store = PeerTaskStore(tmp_path / "colliding.db")
    config = make_config(tmp_path, workspaces={"test": str(tmp_path / "ws")})
    _ra, tok_a = mint(config, "reader")
    _rb, tok_b = mint(config, "reader")
    time.sleep(1.05)
    client = build_client(config, peer_store=store)

    # 1. Creator A submits task with task_id "shared-id"
    r_a = client.post(
        "/tasks",
        headers={**auth(tok_a), "X-Brahma-Node": "node-a"},
        json={
            "task_id": "shared-id",
            "trace_id": "tr-a",
            "root_task_id": "shared-id",
            "origin_node": "node-a",
            "coordinator_node": "node-a",
            "workspace": "test",
            "prompt": "Task A",
        },
    )
    assert r_a.status_code == 202

    # 2. Creator B submits task with SAME task_id "shared-id"
    r_b = client.post(
        "/tasks",
        headers={**auth(tok_b), "X-Brahma-Node": "node-b"},
        json={
            "task_id": "shared-id",
            "trace_id": "tr-b",
            "root_task_id": "shared-id",
            "origin_node": "node-b",
            "coordinator_node": "node-b",
            "workspace": "test",
            "prompt": "Task B",
        },
    )
    assert r_b.status_code == 202

    # 3. Headerless polling resolves each creator's respective task
    poll_a = client.get("/tasks/shared-id", headers=auth(tok_a))
    assert poll_a.status_code == 200
    assert poll_a.json()["task_id"] == "shared-id"
    assert poll_a.json()["origin_node"] == "node-a"

    poll_b = client.get("/tasks/shared-id", headers=auth(tok_b))
    assert poll_b.status_code == 200
    assert poll_b.json()["task_id"] == "shared-id"
    assert poll_b.json()["origin_node"] == "node-b"


def test_peer_server_cryptographic_sas_pairing_exchange(tmp_path: Path):
    """Verify mutual cryptographic SAS pairing handshake and static key pinning."""
    import base64
    from core.peer.identity import NodeIdentity
    from core.peer.pairing import PairingCoordinator, PeerRegistry, PinnedPeer

    node_b = NodeIdentity.create_new(hostname="callee-host")
    reg_b = PeerRegistry(tmp_path / "peers_b.json")
    store_b = PeerTaskStore(tmp_path / "tasks_b.db")

    b_cfg = make_config(
        tmp_path / "b_cfg",
        node_name="Callee Node",
        workspaces={"test": str(tmp_path / "ws")},
    )
    completer = CaptureCompleter("Results from callee")
    client = build_client(
        b_cfg,
        factory=lambda *_args, **_kwargs: completer,
        peer_store=store_b,
        peer_identity=node_b,
        peer_registry=reg_b,
    )

    # 1. Initiator (Node A) creates identity and coordinator
    node_a = NodeIdentity.create_new(hostname="initiator-host")
    reg_a = PeerRegistry(tmp_path / "peers_a.json")
    coord_a = PairingCoordinator(node_a, reg_a)

    # Callee operator token for authorized offer generation
    _rec_op, b_op_tok = mint(b_cfg, "operator", confirm="operator")

    # 2. Unauthenticated /pair/offer is rejected with 401
    r_unauth_offer = client.post("/pair/offer")
    assert r_unauth_offer.status_code == 401

    # Reader and Worker keys without admin/pair scope cannot create pairing offers (P1 hardened)
    _rec_reader, b_reader_tok = mint(b_cfg, "reader")
    r_reader_offer = client.post("/pair/offer", headers=auth(b_reader_tok))
    assert r_reader_offer.status_code == 403

    _rec_worker, b_worker_tok = mint(b_cfg, "worker")
    r_worker_offer = client.post("/pair/offer", headers=auth(b_worker_tok))
    assert r_worker_offer.status_code == 403

    # Authenticated operator /pair/offer succeeds
    r_offer = client.post("/pair/offer", headers=auth(b_op_tok))
    assert r_offer.status_code == 200
    offer_data = r_offer.json()
    assert offer_data["node_id"] == node_b.node_id

    from core.peer.pairing import PairingOffer
    offer = PairingOffer.from_uri(offer_data["offer_uri"])

    # 3. Initiator begins exchange
    a_eph_priv, a_eph_pub, a_static_pub = coord_a.begin_exchange(offer, is_initiator=True)

    # 4. Exchange with unissued offer nonce rejected with 403
    fake_payload = {
        "offer_uri": f"brahma://pair?v=1&node={node_b.node_id}&fp={node_b.spki_sha256}&host=127.0.0.1&port=8010&nonce=fake_unissued_nonce&ts={int(time.time())}",
        "initiator_node_id": node_a.node_id,
        "initiator_spki_sha256": node_a.spki_sha256,
        "initiator_name": "Initiator Node",
        "initiator_endpoint": "http://127.0.0.1:8010",
        "initiator_eph_pub": base64.b64encode(a_eph_pub).decode("ascii"),
        "initiator_static_pub": base64.b64encode(a_static_pub).decode("ascii"),
        "profile": "worker",
    }
    r_fake = client.post("/pair/exchange", json=fake_payload)
    assert r_fake.status_code == 403
    assert r_fake.json()["error"]["code"] == "unauthorized_pairing_offer"

    # Attempting to elevate to operator profile is rejected with 403
    op_payload = {
        "offer_uri": offer.to_uri(),
        "initiator_node_id": node_a.node_id,
        "initiator_spki_sha256": node_a.spki_sha256,
        "initiator_name": "Initiator Node",
        "initiator_endpoint": "http://127.0.0.1:8010",
        "initiator_eph_pub": base64.b64encode(a_eph_pub).decode("ascii"),
        "initiator_static_pub": base64.b64encode(a_static_pub).decode("ascii"),
        "profile": "operator",
    }
    r_op_reject = client.post("/pair/exchange", json=op_payload)
    assert r_op_reject.status_code == 403
    assert r_op_reject.json()["error"]["code"] == "forbidden_peer_profile"

    # Valid pairing exchange against Callee endpoint
    payload = {
        "offer_uri": offer.to_uri(),
        "initiator_node_id": node_a.node_id,
        "initiator_spki_sha256": node_a.spki_sha256,
        "initiator_name": "Initiator Node",
        "initiator_endpoint": "http://127.0.0.1:8010",
        "initiator_eph_pub": base64.b64encode(a_eph_pub).decode("ascii"),
        "initiator_static_pub": base64.b64encode(a_static_pub).decode("ascii"),
        "profile": "worker",
        "workspaces": ["test_ws_a"],
        "labels": {"gpu": "true"},
    }
    r_ex = client.post("/pair/exchange", json=payload)
    assert r_ex.status_code == 200
    ex_resp = r_ex.json()

    # Replaying same offer nonce is rejected (single-use)
    r_replay = client.post("/pair/exchange", json=payload)
    assert r_replay.status_code in (401, 403, 409)

    # 5. Initiator verifies SAS matching
    b_eph_bytes = base64.b64decode(ex_resp["callee_eph_pub"])
    b_static_bytes = base64.b64decode(ex_resp["callee_static_pub"])
    a_sas, session_key = coord_a.complete_key_exchange(
        eph_priv=a_eph_priv,
        peer_node_id=node_b.node_id,
        peer_eph_pub_bytes=b_eph_bytes,
        peer_static_pub_bytes=b_static_bytes,
        peer_spki_sha256=node_b.spki_sha256,
        nonce=offer.nonce,
        is_initiator=True,
    )
    assert a_sas == ex_resp["sas_code"]

    # 6. Initiator unwraps encrypted token delivered by callee
    token_data = coord_a.unwrap_token_payload(
        session_key,
        base64.b64decode(ex_resp["encrypted_token"]),
    )
    assert "token" in token_data
    token_a_for_b = token_data["token"]

    # Peer token cannot access /v1/chat/completions (model surface auth bypass closed)
    r_chat_bypass = client.post(
        "/v1/chat/completions",
        headers=auth(token_a_for_b),
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert r_chat_bypass.status_code == 403
    assert r_chat_bypass.json()["error"]["code"] == "insufficient_scope"

    # 7. Check Callee pinned Node A with static_pub_raw and token="" (not stored as outbound credential)
    callee_pin = reg_b.get_peer(node_a.node_id)
    assert callee_pin is not None
    assert callee_pin.static_pub_raw == base64.b64encode(a_static_pub).decode("ascii")
    assert callee_pin.token == ""
    assert "test_ws_a" in callee_pin.workspaces
    assert callee_pin.labels.get("gpu") == "true"

    # 8. Initiator pins Callee with static_pub_raw
    reg_a.save_peer(
        PinnedPeer(
            node_id=node_b.node_id,
            name="Callee Node",
            endpoint=f"http://{b_cfg.host}:{b_cfg.port}",
            spki_sha256=node_b.spki_sha256,
            token=token_a_for_b,
            profile="worker",
            paired_at="2026-11-10T00:00:00Z",
            static_pub_raw=ex_resp["callee_static_pub"],
            workspaces=ex_resp.get("workspaces", []),
            labels=ex_resp.get("labels", {}),
        )
    )

    # 9. Verify mutating action symmetry: unsigned cancel rejected with 401
    task_payload = {
        "task_id": "pair-task-1",
        "trace_id": "tr-pair-1",
        "root_task_id": "pair-task-1",
        "origin_node": node_a.node_id,
        "coordinator_node": node_a.node_id,
        "workspace": "test",
        "prompt": "Test signed execution",
    }
    body_bytes = json.dumps(task_payload).encode("utf-8")
    now_ts = int(time.time())
    sig_hdr = node_a.sign_request("POST", "/tasks", body_bytes, now_ts, "nonce-pair-1")
    r_task = client.post(
        "/tasks",
        content=body_bytes,
        headers={
            **auth(token_a_for_b),
            "Content-Type": "application/json",
            "X-Brahma-Node": node_a.node_id,
            "X-Brahma-Timestamp": str(now_ts),
            "X-Brahma-Nonce": "nonce-pair-1",
            "X-Brahma-Signature": sig_hdr,
        },
    )
    assert r_task.status_code == 202

    # Unsigned cancel from pinned peer must fail with 401 signature_required
    r_un_cancel = client.post(
        "/tasks/pair-task-1/cancel",
        headers={
            **auth(token_a_for_b),
            "X-Brahma-Node": node_a.node_id,
        },
    )
    assert r_un_cancel.status_code == 401
    assert r_un_cancel.json()["error"]["code"] == "signature_required"


def test_custom_base_dir_auto_resolves_peer_service_and_ledger(tmp_path: Path):
    """Verify create_app with custom base_dir auto-resolves PeerService and enforces ledger."""
    custom_dir = tmp_path / "custom_app_dir"
    cfg = make_config(
        custom_dir,
        node_name="Hermetic Node",
        workspaces={"test": str(custom_dir / "ws")},
    )
    _rec, tok = mint(cfg, "worker", confirm="worker")

    completer = CaptureCompleter("Hermetic run")
    client = build_client(
        cfg,
        factory=lambda *_args, **_kwargs: completer,
    )
    app = client.app
    assert app.state.peer_service is not None
    assert app.state.peer_service.ledger is not None

    # Assert ledger bounds in custom base_dir mode: cap per-node max to 1
    app.state.peer_service.ledger.per_node_max = 1

    req1 = {
        "task_id": "hermetic-task-1",
        "trace_id": "tr-h-1",
        "root_task_id": "hermetic-task-1",
        "origin_node": "origin-node-z",
        "coordinator_node": "origin-node-z",
        "workspace": "test",
        "prompt": "Task 1",
    }
    r1 = client.post("/tasks", headers={**auth(tok), "X-Brahma-Node": "origin-node-z"}, json=req1)
    assert r1.status_code == 202

    req2 = {
        "task_id": "hermetic-task-2",
        "trace_id": "tr-h-2",
        "root_task_id": "hermetic-task-2",
        "origin_node": "origin-node-z",
        "coordinator_node": "origin-node-z",
        "workspace": "test",
        "prompt": "Task 2",
    }
    r2 = client.post("/tasks", headers={**auth(tok), "X-Brahma-Node": "origin-node-z"}, json=req2)
    assert r2.status_code == 503
    assert r2.json()["error"]["code"] == "concurrency_limit_exceeded"


