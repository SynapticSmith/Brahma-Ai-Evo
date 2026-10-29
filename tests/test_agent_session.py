"""Durable agent sessions: round-trip, listing, rewrite after compaction."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.session import AgentSession, SessionNotFound


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    return tmp_path / "agent.sqlite3"


def test_create_then_open_round_trips_metadata(db: Path, tmp_path: Path):
    session = AgentSession.create(tmp_path, title="Fix the parser", model="qwen", db_path=db)
    reopened = AgentSession.open(session.id, db_path=db)

    assert reopened.id == session.id
    assert reopened.title == "Fix the parser"
    assert reopened.model == "qwen"
    assert reopened.cwd == str(tmp_path)


def test_open_missing_session_raises(db: Path):
    with pytest.raises(SessionNotFound):
        AgentSession.open("nope", db_path=db)


def test_messages_round_trip_including_tool_calls(db: Path, tmp_path: Path):
    session = AgentSession.create(tmp_path, db_path=db)
    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": json.dumps({"path": "a.py"})},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "1| x"},
        {"role": "assistant", "content": "done"},
    ]
    for message in messages:
        session.append_message(message)

    assert session.messages() == messages


def test_recent_lists_most_recent_first(db: Path, tmp_path: Path):
    first = AgentSession.create(tmp_path, title="first", db_path=db)
    second = AgentSession.create(tmp_path, title="second", db_path=db)
    second.append_message({"role": "user", "content": "newer"})

    records = AgentSession.recent(db_path=db)
    assert [r.id for r in records][:2] == [second.id, first.id]
    assert records[0].title == "second"


def test_replace_messages_rewrites_the_transcript(db: Path, tmp_path: Path):
    session = AgentSession.create(tmp_path, db_path=db)
    for index in range(5):
        session.append_message({"role": "user", "content": str(index)})

    session.replace_messages([
        {"role": "user", "content": "[Earlier context — compacted]\nsummary"},
        {"role": "user", "content": "latest"},
    ])

    assert [m["content"] for m in session.messages()] == [
        "[Earlier context — compacted]\nsummary",
        "latest",
    ]


def test_status_and_summary_are_persisted(db: Path, tmp_path: Path):
    session = AgentSession.create(tmp_path, db_path=db)
    session.set_status("completed", turns=7)
    session.set_summary("did the thing")

    record = AgentSession.recent(db_path=db)[0]
    assert record.status == "completed"
    assert record.turn_count == 7


def test_events_are_ordered(db: Path, tmp_path: Path):
    session = AgentSession.create(tmp_path, db_path=db)
    session.log_event("turn_start", {"turn": 1})
    session.log_event("tool_call", {"name": "read_file"})

    kinds = [event["kind"] for event in session.events()]
    assert kinds == ["turn_start", "tool_call"]


def test_recent_on_a_missing_database_is_empty(tmp_path: Path):
    assert AgentSession.recent(db_path=tmp_path / "missing.sqlite3") == []
