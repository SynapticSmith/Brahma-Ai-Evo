"""Tests for pinned sessions and session routes (design doc §5.9).

Acceptance criteria:
- A pinned session survives three requests without duplicating turns.
- Stored session wins over replayed history.
- Concurrent requests on the same session ID return 409 session_busy.
- `GET /v1/brahma/sessions` lists recent active/stored sessions.
"""

from __future__ import annotations

import secrets
import threading
import time
from pathlib import Path
from typing import List

from api_support import auth, build_client, make_config, mint
from agent.session import AgentSession


def test_pinned_session_survives_three_requests_without_duplicate_turns(tmp_path: Path):
    config = make_config(tmp_path)
    _rec, token = mint(config, "reader")

    turn_prompts: List[str] = []

    def mock_factory(provider, model, **_kw):
        def completer(messages, tools):
            for m in reversed(messages):
                if m.get("role") == "user":
                    turn_prompts.append(m.get("content", ""))
                    break
            return {"role": "assistant", "content": f"Answer for turn {len(turn_prompts)}"}

        return completer

    client = build_client(config, factory=mock_factory)
    session_id = f"sess_pinned_{secrets.token_hex(6)}"

    # Turn 1
    r1 = client.post(
        "/v1/chat/completions",
        headers={**auth(token), "X-Brahma-Session": session_id},
        json={
            "model": "brahma-evo",
            "messages": [{"role": "user", "content": "First turn"}],
        },
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["brahma"]["session_id"] == session_id

    # Turn 2: client replays prior history plus new turn
    r2 = client.post(
        "/v1/chat/completions",
        headers={**auth(token), "X-Brahma-Session": session_id},
        json={
            "model": "brahma-evo",
            "messages": [
                {"role": "user", "content": "First turn"},
                {"role": "assistant", "content": "Answer for turn 1"},
                {"role": "user", "content": "Second turn"},
            ],
        },
    )
    assert r2.status_code == 200, r2.text

    # Turn 3: client sends only the new turn
    r3 = client.post(
        "/v1/chat/completions",
        headers={**auth(token), "X-Brahma-Session": session_id},
        json={
            "model": "brahma-evo",
            "messages": [{"role": "user", "content": "Third turn"}],
        },
    )
    assert r3.status_code == 200, r3.text

    # Check stored session messages
    session = AgentSession.open(session_id)
    stored = session.messages()
    # Stored messages should be [user1, asst1, user2, asst2, user3, asst3]
    user_msgs = [m["content"] for m in stored if m.get("role") == "user"]
    assert user_msgs == ["First turn", "Second turn", "Third turn"], f"Turn duplication detected: {user_msgs}"


def test_user_field_binds_session_when_header_absent(tmp_path: Path):
    config = make_config(tmp_path)
    _rec, token = mint(config, "reader")
    client = build_client(config)

    session_id = f"sess_user_field_{secrets.token_hex(6)}"
    resp = client.post(
        "/v1/chat/completions",
        headers=auth(token),
        json={
            "model": "brahma-evo",
            "messages": [{"role": "user", "content": "hello"}],
            "user": session_id,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["brahma"]["session_id"] == session_id

    session = AgentSession.open(session_id)
    assert session is not None
    assert len(session.messages()) >= 1


def test_get_sessions_endpoint_returns_list(tmp_path: Path):
    config = make_config(tmp_path)
    _rec, token = mint(config, "reader")
    client = build_client(config)

    session_id = f"sess_list_test_{secrets.token_hex(6)}"
    # Create a session via completion
    client.post(
        "/v1/chat/completions",
        headers={**auth(token), "X-Brahma-Session": session_id},
        json={
            "model": "brahma-evo",
            "messages": [{"role": "user", "content": "ping"}],
        },
    )

    resp = client.get("/v1/brahma/sessions", headers=auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    session_ids = [s["id"] for s in data["data"]]
    assert session_id in session_ids


def test_concurrent_requests_on_same_session_return_409(tmp_path: Path):
    config = make_config(tmp_path)
    _rec, token = mint(config, "reader")

    gate = threading.Event()

    def slow_completer(messages, tools):
        gate.wait(timeout=5)
        return {"role": "assistant", "content": "slow done"}

    client = build_client(config, factory=lambda *a, **kw: slow_completer)
    session_id = f"sess_busy_test_{secrets.token_hex(6)}"

    results = []

    def req():
        r = client.post(
            "/v1/chat/completions",
            headers={**auth(token), "X-Brahma-Session": session_id},
            json={
                "model": "brahma-evo",
                "messages": [{"role": "user", "content": "run"}],
            },
        )
        results.append(r)

    t1 = threading.Thread(target=req)
    t1.start()
    time.sleep(0.1)  # Let t1 start and acquire the session

    # Second request while t1 is waiting
    r2 = client.post(
        "/v1/chat/completions",
        headers={**auth(token), "X-Brahma-Session": session_id},
        json={
            "model": "brahma-evo",
            "messages": [{"role": "user", "content": "run parallel"}],
        },
    )
    gate.set()
    t1.join()

    assert r2.status_code == 409
    body = r2.json()
    assert body["error"]["code"] == "session_busy"
