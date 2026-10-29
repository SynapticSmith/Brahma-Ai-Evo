"""Streaming: assembling deltas, the loop's delta events, and CLI rendering.

Rules under test:

* the assembler merges text and fragmented tool calls into one message;
* streaming is opt-in and falls back loudly when a completer has no transport;
* a retry after partial output emits a ``reset`` so a UI drops stale text;
* the CLI prints text once, never twice, and keeps event lines apart.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.__main__ import _make_printer
from agent.loop import AgentLoop
from agent.permissions import PermissionPolicy
from agent.providers import _endpoint_base, build_completer
from agent.runtime import AgentRuntime
from agent.streaming import (
    StreamAssembler,
    StreamError,
    collect,
    iter_sse,
    openai_stream,
)


# -- the assembler --------------------------------------------------------- #


def _chunk(delta, **extra):
    return {"choices": [{"index": 0, "delta": delta, **extra}]}


def test_text_chunks_accumulate_and_are_reported_in_order():
    assembler = StreamAssembler()

    assert assembler.apply_chunk(_chunk({"content": "Hel"})) == [
        {"kind": "text", "text": "Hel"}
    ]
    assert assembler.apply_chunk(_chunk({"content": "lo"})) == [
        {"kind": "text", "text": "lo"}
    ]
    assert assembler.message()["content"] == "Hello"


def test_tool_call_fragments_merge_by_index_and_keep_model_order():
    assembler = StreamAssembler()

    assembler.apply_chunk(
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 1,
                        "id": "call_b",
                        "function": {"name": "write_file", "arguments": '{"pa'},
                    }
                ]
            }
        )
    )
    assembler.apply_chunk(
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_a",
                        "function": {"name": "read_file", "arguments": '{"path": "a'},
                    }
                ]
            }
        )
    )
    assembler.apply_chunk(
        _chunk(
            {
                "tool_calls": [
                    {"index": 1, "function": {"arguments": 'th": "b"}'}},
                    {"index": 0, "function": {"arguments": '.py"}'}},
                ]
            }
        )
    )

    calls = assembler.message()["tool_calls"]

    assert [call["id"] for call in calls] == ["call_a", "call_b"]
    assert calls[0]["function"] == {
        "name": "read_file",
        "arguments": '{"path": "a.py"}',
    }
    assert calls[1]["function"] == {
        "name": "write_file",
        "arguments": '{"path": "b"}',
    }


def test_a_name_sent_in_fragments_is_joined_once():
    assembler = StreamAssembler()

    assembler.apply_chunk(_chunk({"tool_calls": [{"index": 0, "function": {"name": "get_"}}]}))
    assembler.apply_chunk(_chunk({"tool_calls": [{"index": 0, "function": {"name": "weather"}}]}))
    # A server that repeats the full name must not double it.
    assembler.apply_chunk(_chunk({"tool_calls": [{"index": 0, "function": {"name": "get_weather"}}]}))

    assert assembler.message()["tool_calls"][0]["function"]["name"] == "get_weather"


def test_an_empty_arguments_fragment_still_yields_valid_json():
    assembler = StreamAssembler()

    assembler.apply_chunk(
        _chunk({"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "ping"}}]})
    )

    assert assembler.message()["tool_calls"][0]["function"]["arguments"] == "{}"


def test_a_usage_only_tail_chunk_updates_usage_without_an_event():
    assembler = StreamAssembler()
    assembler.apply_chunk(_chunk({"content": "hi"}))

    assert assembler.apply_chunk({"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2}}) == []
    assert assembler.message()["usage"] == {"prompt_tokens": 4, "completion_tokens": 2}


def test_a_non_delta_message_chunk_is_treated_as_one_piece():
    assembler = StreamAssembler()

    events = assembler.apply_chunk({"choices": [{"message": {"content": "whole"}}]})

    assert events == [{"kind": "text", "text": "whole"}]
    assert assembler.message()["content"] == "whole"


def test_a_chunk_without_choices_is_ignored():
    assert StreamAssembler().apply_chunk({"id": "x"}) == []
    assert StreamAssembler().apply_chunk("not a mapping") == []


def test_an_error_chunk_raises_a_stream_error():
    with pytest.raises(StreamError) as info:
        StreamAssembler().apply_chunk({"error": {"message": "model not found"}})

    assert "model not found" in str(info.value)


def test_finish_reason_is_preserved():
    assembler = StreamAssembler()
    assembler.apply_chunk(_chunk({"content": "x"}, finish_reason="tool_calls"))

    assert assembler.finish_reason == "tool_calls"


def test_collect_forwards_deltas_and_returns_the_final_message():
    chunks = [
        _chunk({"content": "one "}),
        _chunk({"content": "two"}),
        {"choices": [], "usage": {"total_tokens": 9}},
    ]
    seen = []

    message = collect(iter(chunks), seen.append)

    assert message["content"] == "one two"
    assert message["usage"] == {"total_tokens": 9}
    assert [delta["text"] for delta in seen] == ["one ", "two"]


def test_a_broken_delta_sink_does_not_abort_the_call():
    chunks = [_chunk({"content": "still "}), _chunk({"content": "here"})]

    def explode(delta):
        raise RuntimeError("ui is gone")

    assert collect(iter(chunks), explode)["content"] == "still here"


# -- SSE ------------------------------------------------------------------- #


def test_iter_sse_parses_data_lines_and_stops_at_done():
    lines = [
        b": keep-alive\n",
        b"event: message\n",
        b"\n",
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n',
        b"data: not json\n",
        b"data: [DONE]\n",
        b'data: {"choices":[{"delta":{"content":"after"}}]}\n',
    ]

    chunks = list(iter_sse(lines))

    assert len(chunks) == 1
    assert chunks[0]["choices"][0]["delta"]["content"] == "hi"


def test_openai_stream_sends_the_payload_and_yields_chunks(monkeypatch):
    captured = {}

    class FakeResponse:
        def __init__(self):
            self.lines = [b'data: {"choices":[{"delta":{"content":"ok"}}]}\n', b"data: [DONE]\n"]
            self.closed = False

        def __iter__(self):
            return iter(self.lines)

        def close(self):
            self.closed = True

    response = FakeResponse()

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["headers"] = dict(request.header_items())
        return response

    monkeypatch.setattr("agent.streaming.urllib.request.urlopen", fake_urlopen)

    chunks = list(openai_stream("http://host/v1/chat/completions", {"model": "m", "stream": True}))

    assert chunks[0]["choices"][0]["delta"]["content"] == "ok"
    assert captured["body"] == {"model": "m", "stream": True}
    assert captured["url"].endswith("/chat/completions")
    assert response.closed is True


def test_openai_stream_reports_the_http_body(monkeypatch):
    import urllib.error

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 404, "Not Found", {}, __import__("io").BytesIO(b'{"error":"no model"}')
        )

    monkeypatch.setattr("agent.streaming.urllib.request.urlopen", fake_urlopen)

    with pytest.raises(StreamError) as info:
        list(openai_stream("http://host/v1/chat/completions", {}))

    assert "HTTP 404" in str(info.value)
    assert "no model" in str(info.value)


def test_endpoint_base_accepts_a_base_or_a_full_chat_url():
    assert _endpoint_base("https://openrouter.ai/api/v1/chat/completions") == "https://openrouter.ai/api/v1"
    assert _endpoint_base("https://openrouter.ai/api/v1/") == "https://openrouter.ai/api/v1"
    assert _endpoint_base("") == ""


def test_the_stub_completer_streams_its_own_answer():
    completer = build_completer("stub")
    seen = []

    message = completer.stream(
        [{"role": "user", "content": "say hello"}], [], seen.append
    )

    assert message["content"] == completer([{"role": "user", "content": "say hello"}], [])["content"]
    assert "".join(delta["text"] for delta in seen) == message["content"]
    assert len(seen) > 1


# -- the loop -------------------------------------------------------------- #


def _runtime(tmp_path: Path) -> AgentRuntime:
    return AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict(
            {"classes": {"read": "allow", "write": "allow"}, "paths": {"outside_workspace": "deny"}}
        ),
        headless=True,
    )


def _pieces(text: str, count: int = 3) -> list:
    step = max(1, len(text) // count)
    return [text[index : index + step] for index in range(0, len(text), step)]


class StreamScript:
    """A completer that only supports the streaming path."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def __call__(self, messages, tools):  # pragma: no cover - asserted against
        raise AssertionError("the loop used the non-streaming path")

    def stream(self, messages, tools, on_delta):
        self.requests.append([dict(message) for message in messages])
        if not self.turns:
            return {"role": "assistant", "content": "(exhausted)"}
        return self.turns.pop(0)(on_delta)


def _text_turn(text: str):
    def run(on_delta):
        for piece in _pieces(text):
            on_delta({"kind": "text", "text": piece})
        return {"role": "assistant", "content": text}

    return run


def _tool_turn(call_id: str, name: str, args: dict, text: str = ""):
    def run(on_delta):
        if text:
            on_delta({"kind": "text", "text": text})
        on_delta({"kind": "tool_call", "index": 0, "id": call_id, "name": name})
        raw = json.dumps(args)
        for piece in _pieces(raw, 2):
            on_delta({"kind": "tool_call", "index": 0, "arguments": piece})
        return {
            "role": "assistant",
            "content": text,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": raw},
                }
            ],
        }

    return run


def test_streamed_text_reaches_the_result_and_the_transcript(tmp_path: Path):
    script = StreamScript([_text_turn("streamed answer")])
    loop = AgentLoop(
        script,
        runtime=_runtime(tmp_path),
        system="BASE",
        max_turns=3,
        stream=True,
    )

    result = loop.run("hi")

    assert result.text == "streamed answer"
    assert result.stop_reason == "completed"
    deltas = [event for event in result.events if event.get("type") == "delta"]
    assert "".join(event["text"] for event in deltas) == "streamed answer"
    assert all(event["kind"] == "text" for event in deltas)
    assistant = [event for event in result.events if event.get("type") == "assistant"]
    assert assistant[0]["text"] == "streamed answer"
    # Exactly one assistant message, not one per delta.
    assert [m["role"] for m in result.messages] == ["user", "assistant"]
    assert result.messages[-1]["content"] == "streamed answer"


def test_streamed_tool_calls_still_execute_and_are_reported(tmp_path: Path):
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    script = StreamScript(
        [
            _tool_turn("c1", "read_file", {"path": "note.txt"}),
            _text_turn("read it"),
        ]
    )
    loop = AgentLoop(
        script,
        runtime=_runtime(tmp_path),
        system="BASE",
        max_turns=4,
        stream=True,
    )

    result = loop.run("read the note")

    calls = [event for event in result.events if event.get("type") == "tool_call"]
    assert calls[0]["name"] == "read_file"
    fragments = [
        event
        for event in result.events
        if event.get("type") == "delta" and event.get("kind") == "tool_call"
    ]
    assert fragments[0]["name"] == "read_file"
    assert fragments[0]["turn"] == 1
    assert "".join(fragment.get("arguments", "") for fragment in fragments) == '{"path": "note.txt"}'
    assert [m["role"] for m in result.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert result.text == "read it"
    assert result.usage["tool_calls"] == 1


def test_streaming_is_optional_and_falls_back_once(tmp_path: Path):
    calls = []

    def complete(messages, tools):
        calls.append(messages)
        return {"role": "assistant", "content": "plain answer"}

    loop = AgentLoop(complete, runtime=_runtime(tmp_path), system="BASE", max_turns=2, stream=True)

    result = loop.run("hi")

    assert result.text == "plain answer"
    assert len(calls) == 1
    fallbacks = [event for event in result.events if event.get("type") == "stream_fallback"]
    assert len(fallbacks) == 1
    assert "no streaming transport" in fallbacks[0]["reason"]


def test_streaming_off_never_calls_the_streamer(tmp_path: Path):
    script = StreamScript([_text_turn("unused")])

    class Plain:
        def __call__(self, messages, tools):
            return {"role": "assistant", "content": "plain"}

    plain = Plain()
    plain.stream = script.stream
    loop = AgentLoop(plain, runtime=_runtime(tmp_path), system="BASE", max_turns=2)

    assert loop.run("hi").text == "plain"
    assert script.requests == []


def test_a_retry_after_partial_text_resets_it(tmp_path: Path):
    attempts = {"count": 0}

    def flaky(on_delta):
        attempts["count"] += 1
        if attempts["count"] == 1:
            on_delta({"kind": "text", "text": "partial"})
            raise TimeoutError("timed out")
        on_delta({"kind": "text", "text": "final"})
        return {"role": "assistant", "content": "final"}

    class Flaky:
        def __call__(self, messages, tools):  # pragma: no cover
            raise AssertionError("non-streaming path used")

        def stream(self, messages, tools, on_delta):
            return flaky(on_delta)

    loop = AgentLoop(
        Flaky(),
        runtime=_runtime(tmp_path),
        system="BASE",
        max_turns=2,
        stream=True,
        retry_attempts=2,
        retry_base_delay=0.0,
    )

    result = loop.run("hi")

    kinds = [
        (event.get("type"), event.get("kind"))
        for event in result.events
        if event.get("type") in ("delta", "retry")
    ]
    assert kinds == [
        ("delta", "text"),
        ("delta", "reset"),
        ("retry", None),
        ("delta", "text"),
    ]
    assert result.text == "final"
    # The transcript holds the successful attempt only.
    assert [m["content"] for m in result.messages if m["role"] == "assistant"] == ["final"]


# -- CLI rendering --------------------------------------------------------- #


def test_printer_writes_streamed_text_once(capsys):
    printer = _make_printer(as_json=False)

    printer({"type": "turn_start", "turn": 1})
    printer({"type": "delta", "turn": 1, "kind": "text", "text": "Hel"})
    printer({"type": "delta", "turn": 1, "kind": "text", "text": "lo"})
    printer({"type": "assistant", "turn": 1, "text": "Hello", "tool_calls": []})
    printer({"type": "stop", "reason": "completed", "turns": 1})

    out, err = capsys.readouterr()

    assert "Hello" in out
    assert out.count("Hello") == 1
    assert "completed" in out
    # The streamed text is not glued to the stop line.
    assert "Hello\n" in out


def test_printer_prints_a_non_streamed_turn_normally(capsys):
    printer = _make_printer(as_json=False)

    printer({"type": "turn_start", "turn": 1})
    printer({"type": "delta", "turn": 1, "kind": "text", "text": "streamed"})
    printer({"type": "assistant", "turn": 1, "text": "streamed"})
    printer({"type": "turn_start", "turn": 2})
    printer({"type": "assistant", "turn": 2, "text": "second"})

    out, _ = capsys.readouterr()

    assert out.count("streamed") == 1
    assert "second" in out


def test_printer_reports_a_reset_on_stderr(capsys):
    printer = _make_printer(as_json=False)

    printer({"type": "turn_start", "turn": 1})
    printer({"type": "delta", "turn": 1, "kind": "text", "text": "partial"})
    printer({"type": "delta", "turn": 1, "kind": "reset", "reason": "retry"})
    printer({"type": "retry", "attempt": 1, "delay_s": 0.0, "error": "TimeoutError: timed out"})
    printer({"type": "delta", "turn": 1, "kind": "text", "text": "final"})
    printer({"type": "assistant", "turn": 1, "text": "final"})

    out, err = capsys.readouterr()

    assert "partial" in out and "final" in out
    assert "partial output discarded" in err
    # The retry line is not appended to the partial text.
    assert "partial\n" in out


def test_json_mode_passes_deltas_through_untouched(capsys):
    printer = _make_printer(as_json=True)

    printer({"type": "delta", "turn": 1, "kind": "text", "text": "chunk"})

    out, _ = capsys.readouterr()
    assert json.loads(out) == {"type": "delta", "turn": 1, "kind": "text", "text": "chunk"}


def test_printer_records_that_text_was_streamed(capsys):
    printer = _make_printer(as_json=False)
    assert printer.streamed_text is False

    printer({"type": "delta", "turn": 1, "kind": "text", "text": "chunk"})

    assert printer.streamed_text is True


def test_printer_does_not_flag_a_stream_that_never_emitted(capsys):
    printer = _make_printer(as_json=False)

    printer({"type": "delta", "turn": 1, "kind": "reset", "reason": "retry"})
    printer({"type": "assistant", "turn": 1, "text": "whole answer"})

    assert printer.streamed_text is False
    assert "whole answer" in capsys.readouterr().out
