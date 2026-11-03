"""Tests for capability scoping, parsing, and matching engine (core/capabilities.py).

Validates:
1. Parsing of base capabilities and target path/domain patterns.
2. Wildcard matching (global, base prefix, target glob).
3. Capability evaluation and missing-capability reporting.
"""

from __future__ import annotations

from core.capabilities import (
    evaluate_capabilities,
    matches_capability,
    parse_capability,
)


def test_parse_capability():
    base, target = parse_capability("fs.read:C:/test/file.txt")
    assert base == "fs.read"
    assert target == "C:/test/file.txt"

    base2, target2 = parse_capability("process.spawn")
    assert base2 == "process.spawn"
    assert target2 is None

    base3, target3 = parse_capability("net.client:api.google.com:443")
    assert base3 == "net.client"
    assert target3 == "api.google.com:443"


def test_matches_capability_wildcards_and_exact():
    # Global wildcard
    assert matches_capability("*", "fs.read")
    assert matches_capability("*", "process.spawn")

    # Exact match
    assert matches_capability("fs.read", "fs.read")
    assert not matches_capability("fs.read", "fs.write")

    # Base wildcard
    assert matches_capability("fs.*", "fs.read")
    assert matches_capability("fs.*", "fs.write")
    assert not matches_capability("fs.*", "net.client")

    # Target glob matching
    assert matches_capability("fs.read:docs/*", "fs.read:docs/readme.txt")
    assert matches_capability("fs.read:docs/*", "fs.read:docs/sub/readme.txt")
    assert not matches_capability("fs.read:docs/*", "fs.read:src/main.py")

    # Scope wildcard
    assert matches_capability("fs.read:*", "fs.read:anything")
    assert matches_capability("fs.read:*", "fs.read")

    # Unrestricted granted satisfies target request
    assert matches_capability("fs.read", "fs.read:any/path")

    # Restricted granted does NOT satisfy unrestricted request
    assert not matches_capability("fs.read:docs/*", "fs.read")


def test_evaluate_capabilities():
    granted = ["fs.read:workspace/*", "net.client:api.openai.com", "process.eval"]

    # All satisfied
    ok, missing = evaluate_capabilities(granted, ["fs.read:workspace/test.txt", "process.eval"])
    assert ok is True
    assert missing == []

    # Missing capability
    ok2, missing2 = evaluate_capabilities(
        granted,
        ["fs.read:workspace/test.txt", "fs.write:workspace/test.txt", "process.spawn"],
    )
    assert ok2 is False
    assert "fs.write:workspace/test.txt" in missing2
    assert "process.spawn" in missing2
