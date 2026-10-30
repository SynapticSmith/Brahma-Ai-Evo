"""CLI approval colour: opt-in on a TTY, byte-identical when redirected.

Pure Python on purpose. Streams are fakes; nothing here needs a real terminal.
"""
from __future__ import annotations

import os
import re

import pytest

from agent.term import (
    approval_prompt,
    colour_detail,
    colour_enabled,
    prepare_stream,
    supports_color,
)


class _Stream:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _clear_colour_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NO_COLOR", "FORCE_COLOR", "TERM"):
        monkeypatch.delenv(name, raising=False)


def test_colour_detail_marks_the_rule_command_and_diff():
    detail = (
        "rule: exec.ask — approval needed\n"
        "\n"
        "command: rm old.txt\n"
        "cwd: .\n"
        "--- old.txt\n"
        "+++ old.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        " context"
    )

    painted = colour_detail(detail)

    assert "\x1b[1m\x1b[33mrule: exec.ask — approval needed\x1b[0m" in painted
    assert "\x1b[1mcommand:\x1b[0m rm old.txt" in painted
    assert "\x1b[2m--- old.txt\x1b[0m" in painted
    assert "\x1b[2m+++ old.txt\x1b[0m" in painted
    assert "\x1b[36m@@ -1 +1 @@\x1b[0m" in painted
    assert "\x1b[31m-old\x1b[0m" in painted
    assert "\x1b[32m+new\x1b[0m" in painted
    assert "\n context" in painted


def test_plain_lines_are_not_reflowed():
    assert colour_detail("no diff markers here") == "no diff markers here"
    assert colour_detail("") == ""


def test_the_before_preview_heading_and_lines_are_coloured():
    painted = colour_detail("--- before ---\n- old line\n+ new line")

    assert "\x1b[2m--- before ---\x1b[0m" in painted
    assert "\x1b[31m- old line\x1b[0m" in painted
    assert "\x1b[32m+ new line\x1b[0m" in painted


def test_supports_color_requires_a_tty(monkeypatch: pytest.MonkeyPatch):
    _clear_colour_env(monkeypatch)

    assert supports_color(_Stream(True)) is True
    assert supports_color(_Stream(False)) is False


def test_no_color_wins_even_on_a_tty(monkeypatch: pytest.MonkeyPatch):
    _clear_colour_env(monkeypatch)
    monkeypatch.setenv("NO_COLOR", "1")

    assert supports_color(_Stream(True)) is False


def test_no_color_counts_even_when_empty(monkeypatch: pytest.MonkeyPatch):
    """The convention is presence, not a truthy value."""
    _clear_colour_env(monkeypatch)
    monkeypatch.setenv("NO_COLOR", "")

    assert supports_color(_Stream(True)) is False


def test_a_dumb_terminal_is_never_coloured(monkeypatch: pytest.MonkeyPatch):
    _clear_colour_env(monkeypatch)
    monkeypatch.setenv("TERM", "dumb")

    assert supports_color(_Stream(True)) is False


def test_force_color_wins_without_a_tty(monkeypatch: pytest.MonkeyPatch):
    _clear_colour_env(monkeypatch)
    monkeypatch.setenv("FORCE_COLOR", "1")

    assert supports_color(_Stream(False)) is True


@pytest.mark.skipif(os.name == "nt", reason="the Windows path probes a console")
def test_prepare_stream_is_a_noop_off_windows():
    assert prepare_stream(_Stream(True)) is True


def test_colour_enabled_needs_both_halves(monkeypatch: pytest.MonkeyPatch):
    _clear_colour_env(monkeypatch)

    # A TTY on POSIX is enough; on Windows a fake stream cannot be probed, so
    # FORCE_COLOR stands in for "the destination really does understand ANSI".
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert colour_enabled(_Stream(False)) is True

    monkeypatch.setenv("NO_COLOR", "1")
    assert colour_enabled(_Stream(True)) is False


def test_the_plain_prompt_is_the_historical_text():
    prompt = approval_prompt("rule: exec.ask — approval needed", "run_command", colour=False)

    assert "\x1b[" not in prompt
    assert prompt == (
        "\n[approval] rule: exec.ask — approval needed\n"
        "Allow 'run_command'? [y/N/a=always] "
    )


def test_the_coloured_prompt_keeps_the_same_words():
    detail = "rule: exec.ask — approval needed\n\ncommand: rm old.txt"
    plain = approval_prompt(detail, "run_command", colour=False)
    painted = approval_prompt(detail, "run_command", colour=True)

    assert "\x1b[1m\x1b[35m[approval]\x1b[0m" in painted
    assert "\x1b[1mcommand:\x1b[0m rm old.txt" in painted
    assert "Allow 'run_command'? [y/N/a=always]" in painted
    # Stripping the escapes gives the plain prompt back.
    stripped = re.sub(r"\x1b\[[0-9;]*m", "", painted)
    assert stripped == plain
