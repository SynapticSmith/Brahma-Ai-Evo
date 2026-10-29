"""The headless CLI surface (``python -m agent``)."""
from __future__ import annotations

import json
from pathlib import Path

from agent.__main__ import main


def test_run_with_the_stub_provider_emits_ndjson(tmp_path: Path, capsys):
    code = main([
        "run", "say hello",
        "--cwd", str(tmp_path),
        "--provider", "stub",
        "--no-session",
        "--json",
    ])
    out = capsys.readouterr().out.strip().splitlines()

    assert code == 0
    events = [json.loads(line) for line in out]
    assert events[-1]["type"] == "result"
    assert events[-1]["stop_reason"] == "completed"
    assert events[-1]["turns"] == 1
    assert "stub provider" in events[-1]["text"]


def test_run_reads_a_prompt_from_stdin(tmp_path: Path, monkeypatch, capsys):
    import io
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO("piped task"))
    code = main(["run", "--cwd", str(tmp_path), "--provider", "stub", "--no-session", "--json"])
    out = capsys.readouterr().out.strip().splitlines()
    result = json.loads(out[-1])
    assert code == 0
    assert "piped task" in result["text"]


def test_run_without_a_prompt_fails_cleanly(tmp_path: Path, monkeypatch):
    import io
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    code = main(["run", "--cwd", str(tmp_path), "--provider", "stub"])
    assert code == 3


def test_bare_words_are_treated_as_a_run(tmp_path: Path, capsys):
    code = main(["hello", "--cwd", str(tmp_path), "--provider", "stub", "--no-session", "--json"])
    assert code == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["type"] == "result"


def test_stream_prints_the_answer_once_and_emits_delta_events(tmp_path: Path, capsys):
    code = main(
        [
            "run",
            "say hello",
            "--cwd",
            str(tmp_path),
            "--provider",
            "stub",
            "--no-session",
            "--stream",
            "--json",
        ]
    )
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]

    assert code == 0
    deltas = [line for line in lines if line.get("type") == "delta"]
    assert deltas and all(delta["kind"] == "text" for delta in deltas)
    streamed = "".join(delta["text"] for delta in deltas)
    assert streamed == lines[-1]["text"]


def test_stream_human_mode_does_not_repeat_the_answer(tmp_path: Path, capsys):
    code = main(
        [
            "run",
            "say hello",
            "--cwd",
            str(tmp_path),
            "--provider",
            "stub",
            "--no-session",
            "--stream",
        ]
    )
    out, err = capsys.readouterr()

    assert code == 0
    assert out.count("[stub provider]") == 1
    assert "[stopped: completed" in out
    assert "completed · 1 turn(s)" in err


def test_tools_json_lists_the_six_primitives(capsys):
    code = main(["tools", "--json"])
    tools = json.loads(capsys.readouterr().out)
    names = {tool["name"] for tool in tools}
    assert code == 0
    assert {"read_file", "write_file", "edit_file", "glob_files", "grep_files", "run_command"} <= names


def test_permissions_show_reports_the_defaults(capsys, tmp_path: Path):
    code = main(
        ["permissions", "show", "--path", str(tmp_path / "none.json"), "--json"]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["classes"]["exec"] == "ask"
    assert out["headless"]["exec"] == "deny"


def test_permissions_init_refuses_to_clobber_an_existing_file(tmp_path: Path, capsys):
    target = tmp_path / "agent_permissions.json"
    target.write_text('{"classes": {"exec": "allow"}}', encoding="utf-8")

    code = main(["permissions", "init", "--path", str(target)])
    assert code == 3
    assert json.loads(target.read_text(encoding="utf-8"))["classes"]["exec"] == "allow"
    assert "already exists" in capsys.readouterr().err

    code = main(["permissions", "init", "--path", str(target), "--force"])
    assert code == 0
    assert json.loads(target.read_text(encoding="utf-8"))["classes"]["exec"] == "ask"


def test_permissions_init_writes_an_editable_file(tmp_path: Path, capsys):
    target = tmp_path / "agent_permissions.json"
    code = main(["permissions", "init", "--path", str(target)])
    assert code == 0
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["classes"]["read"] == "allow"
    assert "exec" in data["classes"]


def test_sessions_on_an_empty_store_is_not_an_error(capsys):
    code = main(["sessions", "--json"])
    assert code == 0
    assert isinstance(json.loads(capsys.readouterr().out), list)


def test_unknown_subcommand_prints_help():
    code = main([])
    assert code == 3
