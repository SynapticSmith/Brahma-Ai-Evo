"""Subdirectory instruction files: found from the paths a turn touched."""
from __future__ import annotations

import json
from pathlib import Path

from agent.loop import AgentLoop
from agent.permissions import PermissionPolicy
from agent.prompts import (
    MAX_INSTRUCTION_DEPTH,
    MAX_SUBDIR_FILE_CHARS,
    MAX_SUBDIR_INSTRUCTION_CHARS,
    build_system_prompt,
    load_directory_instructions,
    touched_directories,
)
from agent.runtime import AgentRuntime


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _labels(found) -> list[str]:
    return list(found.labels)


def _found(tmp_path: Path, paths, **kwargs):
    return load_directory_instructions(tmp_path, paths, **kwargs)


# -- discovery ------------------------------------------------------------- #


def test_a_touched_file_inherits_its_package_chain_shallow_first(tmp_path: Path):
    _write(tmp_path / "packages" / "AGENTS.md", "PACKAGES RULE")
    _write(tmp_path / "packages" / "foo" / "AGENTS.md", "FOO RULE")

    found = _found(tmp_path, ["packages/foo/app.py"])

    assert _labels(found) == ["packages/AGENTS.md", "packages/foo/AGENTS.md"]
    # Nearest last, so the more specific rules are the final word.
    assert found.text.index("PACKAGES RULE") < found.text.index("FOO RULE")
    assert not found.truncated


def test_a_touched_directory_includes_its_own_instructions(tmp_path: Path):
    package = tmp_path / "packages" / "foo"
    _write(package / "AGENTS.md", "FOO RULE")
    _write(package / "app.py", "print(1)")

    assert _labels(_found(tmp_path, ["packages/foo"])) == ["packages/foo/AGENTS.md"]
    # A non-existent file still resolves through its parent.
    assert _labels(_found(tmp_path, ["packages/foo/new.py"])) == ["packages/foo/AGENTS.md"]


def test_all_three_project_file_names_work_in_a_subdirectory(tmp_path: Path):
    _write(tmp_path / "pkg" / "BRAHMA.md", "BRAHMA SUB")
    _write(tmp_path / "pkg" / ".brahma" / "AGENTS.md", "NESTED SUB")

    found = _found(tmp_path, ["pkg/module.py"])

    assert _labels(found) == ["pkg/BRAHMA.md", "pkg/.brahma/AGENTS.md"]
    assert "BRAHMA SUB" in found.text and "NESTED SUB" in found.text


def test_paths_outside_the_workspace_contribute_nothing(tmp_path: Path):
    workspace = tmp_path / "repo"
    outside = tmp_path / "outside"
    _write(outside / "AGENTS.md", "OUTSIDE")
    workspace.mkdir()

    assert _found(workspace, [str(outside / "file.txt")]).text == ""
    assert _found(workspace, []).text == ""
    assert _found(workspace, ["."]).text == ""


def test_ignored_directories_are_skipped(tmp_path: Path):
    _write(tmp_path / "node_modules" / "AGENTS.md", "VENDOR RULE")
    _write(tmp_path / "dist" / "AGENTS.md", "BUILD RULE")
    _write(tmp_path / ".git" / "AGENTS.md", "GIT RULE")

    for path in ("node_modules/lib/index.js", "dist/bundle.js", ".git/hooks/x"):
        assert _found(tmp_path, [path]).text == ""


def test_a_second_pass_does_not_read_or_emit_the_same_file(tmp_path: Path):
    _write(tmp_path / "pkg" / "AGENTS.md", "PKG RULE")
    seen: set = set()

    first = _found(tmp_path, ["pkg/a.py"], seen=seen)
    second = _found(tmp_path, ["pkg/b.py"], seen=seen)

    assert first.text and not second.text
    assert second.labels == []
    assert len(seen) == 1


def test_an_empty_instruction_file_is_not_retried(tmp_path: Path):
    _write(tmp_path / "pkg" / "AGENTS.md", "   \n")
    seen: set = set()

    assert _found(tmp_path, ["pkg/a.py"], seen=seen).text == ""
    assert seen and _found(tmp_path, ["pkg/b.py"], seen=seen).text == ""


def test_the_walk_below_the_workspace_is_bounded(tmp_path: Path):
    parts = [f"lvl{i}" for i in range(1, MAX_INSTRUCTION_DEPTH + 6)]
    directory = tmp_path
    for part in parts:
        directory = directory / part
        _write(directory / "AGENTS.md", f"RULE {part}")
    (directory / "deep.py").write_text("x", encoding="utf-8")

    found = _found(tmp_path, [str(directory / "deep.py")])

    assert len(found.labels) == MAX_INSTRUCTION_DEPTH
    assert not any(f"lvl{MAX_INSTRUCTION_DEPTH + 1}" in label for label in found.labels)


def test_a_single_huge_file_is_truncated_not_dropped(tmp_path: Path):
    _write(tmp_path / "pkg" / "AGENTS.md", "HEAD RULE\n" + "x" * (MAX_SUBDIR_FILE_CHARS * 2))

    found = _found(tmp_path, ["pkg/a.py"])

    assert found.truncated is True
    assert "[truncated]" in found.text
    assert "HEAD RULE" in found.text
    assert len(found.text) < MAX_SUBDIR_FILE_CHARS + 200


def test_the_character_budget_stops_the_injection(tmp_path: Path):
    _write(tmp_path / "a" / "AGENTS.md", "A" * 200)
    _write(tmp_path / "b" / "AGENTS.md", "B" * 200)

    found = _found(tmp_path, ["a/x.py", "b/x.py"], budget=250)

    assert found.truncated is True
    assert found.labels == ["a/AGENTS.md"]
    assert "[directory instructions truncated]" in found.text
    assert found.text.index("AAA") < found.text.index("truncated")


def test_touched_directories_are_deduplicated_and_ordered(tmp_path: Path):
    _write(tmp_path / "pkg" / "sub" / "AGENTS.md", "SUB")
    dirs = touched_directories(
        tmp_path, ["pkg/sub/a.py", "pkg/sub/b.py", "pkg/other.py"]
    )

    rel = [path.relative_to(tmp_path).as_posix() for path in dirs]
    assert rel == ["pkg", "pkg/sub"]


# -- the loop -------------------------------------------------------------- #


class Script:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages, tools):
        self.calls.append(([dict(m) for m in messages], list(tools)))
        if not self.responses:
            return {"role": "assistant", "content": "(script exhausted)"}
        return self.responses.pop(0)


def _call(call_id: str, name: str, args: dict) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def _runtime(tmp_path: Path, sink=None) -> AgentRuntime:
    return AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict(
            {"classes": {"read": "allow", "write": "allow"}}
        ),
        headless=True,
        event_sink=sink,
    )


def test_a_subdirectory_file_reaches_the_next_request(tmp_path: Path):
    _write(tmp_path / "AGENTS.md", "ROOT RULE")
    _write(tmp_path / "packages" / "foo" / "AGENTS.md", "FOO RULE")
    _write(tmp_path / "packages" / "foo" / "app.py", "print(1)\n")
    events = []
    script = Script(
        [
            _call("c1", "read_file", {"path": "packages/foo/app.py"}),
            {"role": "assistant", "content": "read it"},
        ]
    )
    loop = AgentLoop(
        script,
        runtime=_runtime(tmp_path, sink=events.append),
        system=build_system_prompt(tmp_path, max_parents=0),
        max_turns=5,
    )

    loop.run("inspect packages/foo")

    first_prompt = script.calls[0][0][0]
    second_prompt = script.calls[1][0][0]
    assert first_prompt["role"] == "system"
    assert "FOO RULE" not in first_prompt["content"]
    assert "ROOT RULE" in first_prompt["content"]
    assert "FOO RULE" in second_prompt["content"]
    assert second_prompt["content"].index("ROOT RULE") < second_prompt["content"].index(
        "FOO RULE"
    )

    loaded = [e for e in events if e.get("type") == "instructions"]
    assert loaded == [
        {
            "type": "instructions",
            "loaded": ["packages/foo/AGENTS.md"],
            "chars": len("FOO RULE") + len("# Project instructions (packages/foo/AGENTS.md)\n"),
            "truncated": False,
        }
    ]


def test_the_same_instruction_file_is_injected_only_once(tmp_path: Path):
    _write(tmp_path / "pkg" / "AGENTS.md", "PKG RULE")
    _write(tmp_path / "pkg" / "a.py", "a\n")
    _write(tmp_path / "pkg" / "b.py", "b\n")
    events = []
    script = Script(
        [
            _call("c1", "read_file", {"path": "pkg/a.py"}),
            _call("c2", "read_file", {"path": "pkg/b.py"}),
            {"role": "assistant", "content": "done"},
        ]
    )
    loop = AgentLoop(
        script,
        runtime=_runtime(tmp_path, sink=events.append),
        system="BASE",
        max_turns=5,
    )

    loop.run("read both")

    final_system = script.calls[-1][0][0]["content"]
    assert final_system.count("PKG RULE") == 1
    assert len([e for e in events if e.get("type") == "instructions"]) == 1


def test_a_call_without_a_path_loads_nothing(tmp_path: Path):
    _write(tmp_path / "pkg" / "AGENTS.md", "PKG RULE")
    _write(tmp_path / "pkg" / "a.py", "todo\n")
    events = []
    script = Script(
        [
            _call("c1", "grep_files", {"pattern": "TODO"}),
            {"role": "assistant", "content": "done"},
        ]
    )
    loop = AgentLoop(
        script,
        runtime=_runtime(tmp_path, sink=events.append),
        system="BASE",
        max_turns=5,
    )

    loop.run("find TODO")

    assert not [e for e in events if e.get("type") == "instructions"]
    assert script.calls[-1][0][0]["content"] == "BASE"


def test_directory_instructions_respect_the_run_budget(tmp_path: Path):
    _write(tmp_path / "a" / "AGENTS.md", "A" * (MAX_SUBDIR_INSTRUCTION_CHARS // 2))
    _write(tmp_path / "b" / "AGENTS.md", "B" * (MAX_SUBDIR_INSTRUCTION_CHARS // 2))
    _write(tmp_path / "a" / "x.py", "a\n")
    _write(tmp_path / "b" / "x.py", "b\n")
    events = []
    script = Script(
        [
            _call("c1", "read_file", {"path": "a/x.py"}),
            _call("c2", "read_file", {"path": "b/x.py"}),
            {"role": "assistant", "content": "done"},
        ]
    )
    loop = AgentLoop(
        script,
        runtime=_runtime(tmp_path, sink=events.append),
        system="BASE",
        max_turns=5,
    )

    loop.run("read both")

    final_system = script.calls[-1][0][0]["content"]
    loaded = [e for e in events if e.get("type") == "instructions"]
    # The first file consumed its half; the second no longer fits whole.
    assert len(loaded) == 2
    assert "A" * 100 in final_system
    assert "B" * 100 not in final_system
    assert "directory instructions truncated" in final_system
    assert loaded[-1]["truncated"] is True
