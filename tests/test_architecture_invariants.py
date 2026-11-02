"""Architecture invariants and ratchets (ARCHITECTURE-SPEC §10, §12 Q1).

The ratchet file holds ceilings, not targets: a file may shrink freely, and
raising a ceiling is a deliberate edit that the spec asks to justify with an
ADR.
"""

import ast
import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RATCHET = json.loads(
    (Path(__file__).parent / "architecture_ratchet.json").read_text(encoding="utf-8")
)

# The pre-harness autonomous stack. `agent_task` used to reach all four; the
# harness (`AgentLoop` + `run_subagent`) replaces them.
LEGACY_MODULES = ("planner.py", "executor.py", "task_queue.py", "error_handler.py")


def _line_count(relative_path: str) -> int:
    return len((ROOT / relative_path).read_text(encoding="utf-8").splitlines())


def _elif_count(relative_path: str) -> int:
    count = 0
    for line in (ROOT / relative_path).read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("elif ") and len(line) - len(stripped) >= 4:
            count += 1
    return count


def test_legacy_autonomous_stack_is_gone():
    present = [name for name in LEGACY_MODULES if (ROOT / "agent" / name).exists()]
    assert present == [], (
        "the legacy autonomous executor is back or was never deleted: "
        + ", ".join(present)
    )


def test_agent_package_init_stays_inert():
    # A module docstring is fine; anything executable (an import, a constant, a
    # side effect) is not: `python -m agent` must not pull PyQt or a provider.
    text = (ROOT / "agent" / "__init__.py").read_text(encoding="utf-8")
    body = ast.parse(text).body
    code = [
        node
        for node in body
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
    ]
    assert code == [], "agent/__init__.py must stay inert (docstring only)"


def test_line_count_ratchet():
    grown = {
        path: (_line_count(path), ceiling)
        for path, ceiling in RATCHET["lines"].items()
        if _line_count(path) > ceiling
    }
    assert grown == {}, (
        "these files grew past their ratchet ceiling: "
        + ", ".join(f"{p} {n}>{c}" for p, (n, c) in grown.items())
    )


def test_native_elif_ratchet():
    for path, ceiling in RATCHET["elif_branches"].items():
        count = _elif_count(path)
        assert count <= ceiling, (
            f"{path} has {count} native `elif` branches (ceiling {ceiling}); "
            "a new tool must be a ToolSpec, not another branch"
        )


def test_agent_import_canary_is_stdlib_only():
    script = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, sys.argv[1])
        import agent
        import agent.code_tools
        import agent.context
        import agent.loop
        import agent.mcp
        import agent.permissions
        import agent.prompts
        import agent.process
        import agent.providers
        import agent.runtime
        import agent.session
        import agent.skills
        import agent.spec
        import agent.subagent

        forbidden = (
            "PyQt", "google.genai", "google.generativeai", "openai",
            "anthropic", "win32com", "comtypes",
        )
        bad = sorted(name for name in sys.modules if name.startswith(forbidden))
        assert not bad, bad
        sys.stdout.write("AGENT_OK\\n")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(ROOT)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "AGENT_OK" in proc.stdout, (
        f"agent/ is no longer importable without PyQt/provider SDKs (rc={proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr[-3000:]}"
    )
