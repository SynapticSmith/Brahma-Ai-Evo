"""Unit tests for tools/scripts_index.py static analysis and verification engine."""
from pathlib import Path

from tools.scripts_index import (
    analyze_script,
    build_index,
    check_index,
    compute_sha256,
)


def test_compute_sha256(tmp_path: Path):
    sample = tmp_path / "sample.py"
    sample.write_text("print('hello world')", encoding="utf-8")
    h = compute_sha256(sample)
    assert len(h) == 64
    assert isinstance(h, str)


def test_analyze_script_extracts_docstring_and_args(tmp_path: Path):
    script = tmp_path / "my_tool.py"
    script.write_text(
        '''"""A sample tool for testing AST analysis.

Detailed multiline explanation.
"""
import argparse
import sqlite3

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10, help="Max items")
    parser.add_argument("--verbose", action="store_true", help="Verbose flag")
    args = parser.parse_args()
    conn = sqlite3.connect(":memory:")

if __name__ == "__main__":
    main()
''',
        encoding="utf-8",
    )

    info = analyze_script(script)
    assert info["id"] == "my-tool"
    assert info["name"] == "my_tool.py"
    assert "sample tool" in info["summary"]
    assert info["safety"] in ("workspace_write", "read_write_local")
    assert "sqlite3" in info["requires"]
    assert any(a["name"] == "--limit" for a in info["args"])
    assert any(a["name"] == "--verbose" for a in info["args"])


def test_build_and_check_index(tmp_path: Path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    s1 = tools_dir / "tool_alpha.py"
    s1.write_text('"""Alpha tool."""\nprint("alpha")', encoding="utf-8")

    out_file = tmp_path / "config" / "scripts.json"
    records, count = build_index(tools_dir=tools_dir, output_path=out_file)

    assert count == 1
    assert len(records) == 1
    assert out_file.is_file()

    ok, errors = check_index(tools_dir=tools_dir, index_path=out_file)
    assert ok is True
    assert len(errors) == 0

    # Modify script without rebuilding -> should detect drift
    s1.write_text('"""Alpha modified."""\nprint("alpha v2")', encoding="utf-8")
    ok2, errors2 = check_index(tools_dir=tools_dir, index_path=out_file)
    assert ok2 is False
    assert any("Stale script record" in err for err in errors2)
