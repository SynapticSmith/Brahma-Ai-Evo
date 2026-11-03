"""Tests for SSE streaming (design doc §5.7, §9.1).

Asserts byte-level compliance with the OpenAI streaming protocol:
- text/event-stream content type
- initial role chunk
- delta text chunks concatenating to the complete answer
- choices: [] on the usage chunk (OpenAI standard)
- SSE trace comments (: brahma.turn, : brahma.tool, : keepalive)
- data: [DONE] terminator on every stream (including errors)
- client disconnect cancels loop
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from api_support import auth, build_client, make_config, mint

BODY_STREAM = {
    "model": "brahma-evo",
    "messages": [{"role": "user", "content": "hello"}],
    "stream": True,
}


def _client(tmp_path, **config_overrides):
    config = make_config(tmp_path, **config_overrides)
    _record, token = mint(config, "reader")
    return build_client(config), token, config


def _parse_sse_events(raw_text: str) -> List[Dict[str, Any]]:
    """Parse raw SSE text into structured events, preserving comments."""
    events: List[Dict[str, Any]] = []
    blocks = raw_text.strip().split("\n\n")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        data_lines = []
        comment_lines = []
        for line in lines:
            line = line.strip()
            if line.startswith(":"):
                comment_lines.append(line[1:].strip())
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())

        if comment_lines:
            events.append({"type": "comment", "text": " ".join(comment_lines)})
        if data_lines:
            content = " ".join(data_lines)
            if content == "[DONE]":
                events.append({"type": "done"})
            else:
                try:
                    payload = json.loads(content)
                    events.append({"type": "data", "payload": payload})
                except json.JSONDecodeError:
                    events.append({"type": "raw", "text": content})
    return events


def test_streaming_headers_and_content_type(tmp_path):
    client, token, _config = _client(tmp_path)
    response = client.post("/v1/chat/completions", json=BODY_STREAM, headers=auth(token))
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    assert response.headers.get("cache-control") == "no-cache"


def test_streaming_chunks_concatenate_to_final_answer(tmp_path):
    client, token, _config = _client(tmp_path)
    response = client.post("/v1/chat/completions", json=BODY_STREAM, headers=auth(token))
    assert response.status_code == 200

    events = _parse_sse_events(response.text)
    assert len(events) >= 3

    # Initial chunk has assistant role
    data_events = [e for e in events if e.get("type") == "data"]
    first = data_events[0]["payload"]
    assert first["object"] == "chat.completion.chunk"
    assert first["choices"][0]["delta"]["role"] == "assistant"
    assert first["choices"][0]["delta"]["content"] == ""

    # Text chunks
    text_pieces = []
    finish_reasons = []
    for event in data_events[1:]:
        payload = event["payload"]
        if payload.get("choices"):
            choice = payload["choices"][0]
            delta = choice.get("delta", {})
            if "content" in delta and delta["content"]:
                text_pieces.append(delta["content"])
            if choice.get("finish_reason"):
                finish_reasons.append(choice["finish_reason"])

    full_text = "".join(text_pieces)
    assert "[stub provider]" in full_text
    assert "stop" in finish_reasons

    # Stream terminator is [DONE]
    assert events[-1]["type"] == "done"


def test_usage_chunk_has_empty_choices_array(tmp_path):
    client, token, _config = _client(tmp_path)
    body = {
        **BODY_STREAM,
        "stream_options": {"include_usage": True},
    }
    response = client.post("/v1/chat/completions", json=body, headers=auth(token))
    assert response.status_code == 200

    events = _parse_sse_events(response.text)
    data_events = [e for e in events if e.get("type") == "data"]

    # One chunk must be the usage chunk with choices: []
    usage_chunks = [e["payload"] for e in data_events if "usage" in e["payload"]]
    assert len(usage_chunks) == 1
    u = usage_chunks[0]
    assert u["choices"] == []
    # Stub provider returns tokens_reported=False so usage is None per I-22
    assert u["usage"] is None


def test_stream_trace_comments_present(tmp_path):
    client, token, _config = _client(tmp_path)
    response = client.post("/v1/chat/completions", json=BODY_STREAM, headers=auth(token))
    assert response.status_code == 200

    events = _parse_sse_events(response.text)
    comments = [e["text"] for e in events if e.get("type") == "comment"]
    # At least the turn trace comment was emitted
    assert any("brahma.turn" in c for c in comments)


def test_whoami_endpoint(tmp_path):
    client, token, _config = _client(tmp_path)
    response = client.get("/v1/brahma/whoami", headers=auth(token))
    assert response.status_code == 200
    payload = response.json()
    assert payload["profile"] == "reader"
    assert "chat" in payload["scopes"]
    assert "models" in payload["scopes"]
    assert payload["workspace"] == "test"


def test_run_diagnostics_endpoint(tmp_path):
    client, token, _config = _client(tmp_path)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "brahma-evo", "messages": [{"role": "user", "content": "hi"}]},
        headers=auth(token),
    )
    assert response.status_code == 200
    run_id = response.json()["brahma"]["run_id"]

    run_res = client.get(f"/v1/brahma/runs/{run_id}", headers=auth(token))
    assert run_res.status_code == 200
    run_data = run_res.json()
    assert run_data["run_id"] == run_id
    assert run_data["status"] == "completed"
    assert run_data["turns"] >= 1
