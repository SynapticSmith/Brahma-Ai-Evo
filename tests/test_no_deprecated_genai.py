"""Regression tests: the deprecated ``google.generativeai`` package must stay gone.

Every Gemini text call used to import ``google.generativeai``, which now prints a
``FutureWarning`` on import ("All support for the ``google.generativeai`` package
has ended..."). The repo standardises on the supported ``google.genai`` SDK, so
these tests guard both against reintroducing the deprecated package and against
the import warning coming back.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Import / usage shapes only -- a bare ``google.generativeai`` inside a comment is
# fine (and is what the migration note in actions/file_processor.py is).
FORBIDDEN = (
    "import google.generativeai",
    "genai.configure(",
    "genai.GenerativeModel(",
)

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "docs"}

# This file necessarily contains the forbidden literals; never flag itself.
SELF = Path(__file__).resolve()

MIGRATED = (
    "actions.file_processor",
    "actions.docx_tools",
    "actions.mobile_autopilot",
    "actions.pdf_tools",
    "agent.executor",
    "agent.error_handler",
    "agent.planner",
)


def test_no_module_uses_the_deprecated_package():
    offenders = []
    for path in ROOT.rglob("*.py"):
        if path.resolve() == SELF:
            continue
        if SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in FORBIDDEN:
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)} -> {needle}")
    assert not offenders, "deprecated google.generativeai usage:\n" + "\n".join(offenders)


def test_migrated_modules_import_without_the_deprecation_warning():
    """Import each migrated module with FutureWarning as an error."""
    script = textwrap.dedent(
        """
        import importlib, sys, warnings
        sys.path.insert(0, sys.argv[1])
        for name in sys.argv[2:]:
            with warnings.catch_warnings():
                warnings.simplefilter("error", FutureWarning)
                importlib.import_module(name)
        assert "google.generativeai" not in sys.modules, (
            "the deprecated package was imported after all"
        )
        sys.stdout.write("GENAI_OK\\n")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(ROOT), *MIGRATED],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert "GENAI_OK" in proc.stdout, (
        f"deprecation warning / deprecated import (rc={proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr[-3000:]}"
    )
