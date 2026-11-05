"""Tests for the passthrough model lane (design doc §5.4, §5.8).

Acceptance criteria:
- Cline and Continue both work end-to-end against `brahma-evo:passthrough` with their own tools.
- Client tools are forwarded to the upstream completer verbatim.
- Upstream tool calls are surfaced in choices[0].message.tool_calls with finish_reason="tool_calls".
- Client history (including tool messages and prior tool_calls) is preserved verbatim.
- Agent lane continues to reject client tools with 400 tools_not_supported.
- Passthrough does not require a local workspace directory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from api_support import auth, build_client, make_config, mint


SAMPLE_CLIENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }
]


def test_passthrough_accepts_client_tools_and_forwards_them(tmp_path: Path):
    config = make_config(tmp_path)
    _rec, token = mint(config, "reader")

    captured_tools: List[Dict[str, Any]] = []
    captured_messages: List[Dict[str, Any]] = []

    def mock_factory(provider, model, **_kw):
        def completer(messages, tools):
            captured_tools.extend(tools)
            captured_messages.extend(messages)
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": "src/main.py"}),
                        },
                    }
                ],
            }

        return completer

    client = build_client(config, factory=mock_factory)
    payload = {
        "model": "brahma-evo:passthrough",
        "messages": [
            {"role": "user", "content": "Please read src/main.py"},
        ],
        "tools": SAMPLE_CLIENT_TOOLS,
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }
    response = client.post("/v1/chat/completions", headers=auth(token), json=payload)
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["choices"][0]["finish_reason"] == "tool_calls"
    assert data["choices"][0]["message"]["role"] == "assistant"
    tool_calls = data["choices"][0]["message"]["tool_calls"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "call_abc123"
    assert tool_calls[0]["function"]["name"] == "read_file"
    assert json.loads(tool_calls[0]["function"]["arguments"]) == {"path": "src/main.py"}
    assert data["brahma"]["lane"] == "passthrough"
    assert data["brahma"]["tool_calls"] == 1

    # Verify completer received the client's tools
    assert len(captured_tools) == 1
    assert captured_tools[0]["function"]["name"] == "read_file"


def test_passthrough_preserves_wire_history_verbatim(tmp_path: Path):
    config = make_config(tmp_path)
    _rec, token = mint(config, "reader")

    received_messages: List[Dict[str, Any]] = []

    def mock_factory(provider, model, **_kw):
        def completer(messages, tools):
            received_messages.extend(messages)
            return {"role": "assistant", "content": "File content looks good."}

        return completer

    client = build_client(config, factory=mock_factory)
    payload = {
        "model": "brahma-evo:passthrough",
        "messages": [
            {"role": "user", "content": "Please inspect index.html"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "index.html"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "<html><body>Hello</body></html>",
            },
            {"role": "user", "content": "What does it do?"},
        ],
        "tools": SAMPLE_CLIENT_TOOLS,
    }
    response = client.post("/v1/chat/completions", headers=auth(token), json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "File content looks good."
    assert response.json()["choices"][0]["finish_reason"] == "stop"

    # Verify tool call and tool result survived in wire messages to completer
    tool_msg = next((m for m in received_messages if m.get("role") == "tool"), None)
    assert tool_msg is not None
    assert tool_msg["tool_call_id"] == "call_1"
    assert tool_msg["content"] == "<html><body>Hello</body></html>"

    asst_msg = next((m for m in received_messages if m.get("role") == "assistant"), None)
    assert asst_msg is not None
    assert asst_msg.get("tool_calls") is not None


def test_passthrough_streaming_delivers_tool_calls(tmp_path: Path):
    config = make_config(tmp_path)
    _rec, token = mint(config, "reader")

    def mock_factory(provider, model, **_kw):
        def complete(messages, tools):
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_stream_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'},
                    }
                ],
            }

        def stream(messages, tools, on_delta):
            on_delta({
                "kind": "tool_call",
                "index": 0,
                "id": "call_stream_1",
                "name": "read_file",
                "arguments": '{"path": "a.txt"}',
            })
            return complete(messages, tools)

        complete.stream = stream
        return complete

    client = build_client(config, factory=mock_factory)
    payload = {
        "model": "brahma-evo:passthrough",
        "stream": True,
        "messages": [{"role": "user", "content": "read a.txt"}],
        "tools": SAMPLE_CLIENT_TOOLS,
    }
    response = client.post("/v1/chat/completions", headers=auth(token), json=payload)
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    lines = response.text.strip().split("\n")
    data_lines = [line[len("data: ") :] for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    chunks = [json.loads(line) for line in data_lines]

    # Find the delta with tool_calls
    tc_chunks = [c for c in chunks if c.get("choices") and c["choices"][0].get("delta", {}).get("tool_calls")]
    assert len(tc_chunks) >= 1
    tc = tc_chunks[0]["choices"][0]["delta"]["tool_calls"][0]
    assert tc["id"] == "call_stream_1"
    assert tc["function"]["name"] == "read_file"

    # Finish chunk should have finish_reason == "tool_calls"
    finish_chunks = [c for c in chunks if c.get("choices") and c["choices"][0].get("finish_reason")]
    assert len(finish_chunks) >= 1
    assert finish_chunks[0]["choices"][0]["finish_reason"] == "tool_calls"


def test_agent_lane_refuses_client_tools_with_specific_error(tmp_path: Path):
    config = make_config(tmp_path)
    _rec, token = mint(config, "reader")
    client = build_client(config)

    payload = {
        "model": "brahma-evo",
        "messages": [{"role": "user", "content": "do something"}],
        "tools": SAMPLE_CLIENT_TOOLS,
    }
    response = client.post("/v1/chat/completions", headers=auth(token), json=payload)
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "tools_not_supported"
    assert "brahma-evo:passthrough" in body["error"]["message"]


def test_passthrough_extracts_provider_usage(tmp_path: Path):
    config = make_config(tmp_path)
    _rec, token = mint(config, "reader")

    def mock_factory(provider, model, **_kw):
        def complete(messages, tools):
            return {
                "role": "assistant",
                "content": "Hello with usage",
                "usage": {
                    "prompt_tokens": 15,
                    "completion_tokens": 25,
                    "total_tokens": 40,
                },
            }

        return complete

    client = build_client(config, factory=mock_factory)
    payload = {
        "model": "brahma-evo:passthrough",
        "messages": [{"role": "user", "content": "hello"}],
    }
    response = client.post("/v1/chat/completions", headers=auth(token), json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["usage"] == {
        "prompt_tokens": 15,
        "completion_tokens": 25,
        "total_tokens": 40,
    }
    assert data["brahma"]["tokens_reported"] is True


def test_passthrough_extracts_gemini_usage_metadata(tmp_path: Path):
    config = make_config(tmp_path)
    _rec, token = mint(config, "reader")

    def mock_factory(provider, model, **_kw):
        def complete(messages, tools):
            return {
                "role": "assistant",
                "content": "Hello Gemini usage",
                "usage_metadata": {
                    "prompt_token_count": 12,
                    "candidates_token_count": 18,
                    "total_token_count": 30,
                },
            }

        return complete

    client = build_client(config, factory=mock_factory)
    payload = {
        "model": "brahma-evo:passthrough",
        "messages": [{"role": "user", "content": "hello"}],
    }
    response = client.post("/v1/chat/completions", headers=auth(token), json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 18,
        "total_tokens": 30,
    }
    assert data["brahma"]["tokens_reported"] is True

