"""The architecture guard from design doc §9.4.

The port is a transport, not a door. These assertions fail if the package ever
grows a second way to reach a tool, a process, or a desktop action -- which is
exactly the drift that makes "it goes through AgentRuntime.execute" untrue.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "brahma_api"

FORBIDDEN = (
    "import actions",
    "from actions",
    "desktop_tools",
    "spec.execute",
    "import subprocess",
    "from subprocess",
    "subprocess.run(",
    "subprocess.Popen(",
    "os.system(",
)


def _sources():
    return sorted(PACKAGE.glob("*.py"))


def test_package_exists_and_init_is_empty():
    assert PACKAGE.is_dir()
    assert (PACKAGE / "__init__.py").read_text(encoding="utf-8") == ""


def test_no_tool_dispatch_or_process_escape():
    hits = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN:
            if needle in text:
                hits.append(f"{path.name}: {needle}")
    assert hits == [], "brahma_api must not reach tools or processes directly:\n" + "\n".join(hits)


def test_no_tool_shaped_route():
    from brahma_api.config import BrahmaApiConfig
    from brahma_api.server import create_app

    config = BrahmaApiConfig(base_dir=ROOT, audit=False)
    app = create_app(config)
    paths = [getattr(route, "path", "") for route in app.routes]
    assert [path for path in paths if "/tools/" in path] == []
    assert "/v1/chat/completions" in paths
    assert "/v1/models" in paths


def test_main_py_does_not_own_a_second_port_yet():
    """Phase 0 runs standalone; a GUI lifecycle belongs in Phase 1's finally."""
    text = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "brahma_api" not in text
