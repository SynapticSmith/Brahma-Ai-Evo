"""The GUI text path's bridge to durable agent sessions.

Pure Python on purpose: the harness CI installs only pytest, so this file must
not import PyQt or a provider SDK.
"""
from __future__ import annotations

from pathlib import Path

from agent.context import ConversationContext
from agent.session import AgentSession
from agent import session_bridge as bridge


def _db(tmp_path: Path) -> Path:
    return tmp_path / "agent.sqlite3"


def test_title_collapses_whitespace_and_truncates():
    assert bridge.title_for("  fix\n the\tparser  ") == "fix the parser"
    long = "x" * 100
    title = bridge.title_for(long, limit=20)
    assert len(title) == 20
    assert title.endswith("\u2026")


def test_split_system_only_consumes_a_leading_system_message():
    system, rest = bridge.split_system(
        [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "go"},
        ]
    )
    assert system == "rules"
    assert rest == [{"role": "user", "content": "go"}]

    system, rest = bridge.split_system([{"role": "user", "content": "go"}])
    assert system is None
    assert rest == [{"role": "user", "content": "go"}]


def test_history_strips_the_stored_system_prompt(tmp_path: Path):
    session = AgentSession.create(tmp_path, db_path=_db(tmp_path))
    session.append_message({"role": "system", "content": "old rules"})
    session.append_message({"role": "user", "content": "first"})

    assert bridge.history(session) == [{"role": "user", "content": "first"}]
    assert bridge.history(None) == []


def test_seed_context_prefers_the_fresh_system_prompt():
    context = bridge.seed_context(
        [
            {"role": "system", "content": "old rules"},
            {"role": "user", "content": "first"},
        ],
        system="new rules",
    )
    assert isinstance(context, ConversationContext)
    assert context.system == "new rules"
    assert context.messages == [{"role": "user", "content": "first"}], (
        "the stored system row must not appear twice"
    )


def test_messages_after_appends_only_the_new_tail():
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
    ]
    seeded = [*history, {"role": "user", "content": "second"}]

    assert bridge.messages_after(history, seeded) == [
        {"role": "user", "content": "second"}
    ]
    assert bridge.messages_after([], seeded) == seeded
    # A compacted history stops matching early; the tail is still new material.
    assert bridge.messages_after(
        [{"role": "user", "content": "[Earlier context — compacted]\nsummary"}],
        seeded,
    ) == seeded


def test_transcript_keeps_conversation_and_drops_execution_detail():
    turns = bridge.transcript(
        [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "fix it"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "read_file"}}],
            },
            {"role": "tool", "name": "read_file", "content": "data"},
            {"role": "assistant", "content": "fixed"},
            {"role": "user", "content": "[Earlier context — compacted]\nsummary"},
        ]
    )
    assert turns == [
        {"role": "user", "text": "fix it"},
        {"role": "assistant", "text": "fixed"},
        {"role": "system", "text": "Earlier context was compacted."},
    ]


def test_resumed_history_seeds_the_next_turn_without_duplicating_it(tmp_path: Path):
    db = _db(tmp_path)
    session = AgentSession.create(tmp_path, title="build", db_path=db)
    session.append_message({"role": "user", "content": "first"})
    session.append_message({"role": "assistant", "content": "one"})

    reopened = AgentSession.open(session.id, db_path=db)
    seeded = [*bridge.history(reopened), {"role": "user", "content": "second"}]
    context = bridge.seed_context(
        [{"role": "system", "content": "fresh rules"}, *seeded]
    )

    pending = bridge.messages_after(bridge.history(reopened), context.messages)
    for message in pending:
        reopened.append_message(message)
    # The loop appends the model's reply afterwards.
    reopened.append_message({"role": "assistant", "content": "two"})

    assert reopened.messages() == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "two"},
    ]
    assert context.as_payload()[0] == {"role": "system", "content": "fresh rules"}


def test_voice_turn_messages_collapse_whitespace_and_skip_empty_sides():
    assert bridge.voice_turn_messages("  set\n a\t timer ", "") == [
        {"role": "user", "content": "set a timer"}
    ]
    assert bridge.voice_turn_messages("", "  Timer\n set.  ") == [
        {"role": "assistant", "content": "Timer set."}
    ]
    assert bridge.voice_turn_messages("", "   ") == []


def test_record_voice_turn_appends_a_pair_and_counts_user_turns(tmp_path: Path):
    db = _db(tmp_path)
    session = AgentSession.create(
        tmp_path, title="voice chat", model=bridge.VOICE_MODEL, db_path=db
    )

    assert bridge.record_voice_turn(session, "set a timer", "Timer set.") == 2
    # A tool-only turn may have no spoken answer; the user side still lands.
    assert bridge.record_voice_turn(session, "thanks", "") == 1

    assert session.messages() == [
        {"role": "user", "content": "set a timer"},
        {"role": "assistant", "content": "Timer set."},
        {"role": "user", "content": "thanks"},
    ]
    record = AgentSession.recent(db_path=db)[0]
    assert record.turn_count == 2
    assert record.model == bridge.VOICE_MODEL
    assert record.status == "active"


def test_a_voice_turn_replays_and_seeds_the_next_text_turn(tmp_path: Path):
    session = AgentSession.create(tmp_path, db_path=_db(tmp_path))
    bridge.record_voice_turn(session, "what is on my screen", "A code editor.")

    assert bridge.transcript(session.messages()) == [
        {"role": "user", "text": "what is on my screen"},
        {"role": "assistant", "text": "A code editor."},
    ]
    # Switching from speech to typing carries the spoken turn into the seed.
    assert bridge.history(session) == [
        {"role": "user", "content": "what is on my screen"},
        {"role": "assistant", "content": "A code editor."},
    ]


def test_record_voice_turn_never_breaks_audio(tmp_path: Path):
    class Broken:
        def append_message(self, message):  # pragma: no cover - raises first
            raise RuntimeError("database is locked")

    assert bridge.record_voice_turn(None, "hi", "hello") == 0
    assert bridge.record_voice_turn(Broken(), "hi", "hello") == 0


def test_picker_and_summary_labels_stay_one_line(tmp_path: Path):
    db = _db(tmp_path)
    session = AgentSession.create(
        tmp_path, title="a\nmulti-line " + "t" * 100, db_path=db
    )
    session.set_status("completed", turns=4)
    record = AgentSession.recent(db_path=db)[0]

    label = bridge.picker_label(record)
    summary = bridge.session_summary(record)

    assert "\n" not in label and "\n" not in summary
    assert "4 turns" in label and "completed" in label
    assert session.id in summary
    assert record.id[:8] in label
