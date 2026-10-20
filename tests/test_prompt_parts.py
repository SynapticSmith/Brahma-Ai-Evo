"""Two-tier prompt: the core/manual split, tier selection, and the manual tool.

The prompt file is the agent's whole operational brief (``core/prompt.txt``).
These tests pin the contract the loaders depend on:

* the file carries exactly one ``--- MANUAL ---`` marker and both halves are
  non-empty, so ``load_prompt_tiers`` is never guessing;
* the resident core keeps the sections behaviour depends on (turn loop,
  principles, capability inventory, discovery rules, memory, safety) and does
  NOT carry the token-heavy reference half;
* the on-demand manual keeps the material the ``get_tool_surface`` handler and
  the docs promise (the ``SKILLS LIBRARY`` header among it);
* tier selection: the lean core is the default, ``full`` restores the combined
  prompt, ``auto`` picks the core for a same-machine endpoint.

No network, no models, no clock: pure file and function checks.
"""

from pathlib import Path

import core.prompt_parts as pp

REPO = Path(__file__).resolve().parent.parent
PROMPT_PATH = REPO / "core" / "prompt.txt"


def _prompt_text() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def test_prompt_file_is_one_document_with_exactly_one_marker():
    lines = [ln.strip() for ln in _prompt_text().splitlines()]
    assert lines.count(pp.MANUAL_MARKER) == 1, (
        "core/prompt.txt must separate the resident core from the on-demand "
        "manual with exactly one '--- MANUAL ---' line"
    )


def test_both_tiers_are_substantial():
    core, manual = pp.load_prompt_tiers(PROMPT_PATH)
    assert len(core) > 1500, "the resident core must carry real behaviour"
    assert len(manual) > 3000, f"the manual lost its content ({len(manual)} chars)"


def test_resident_core_keeps_behaviour_and_drops_the_routing_table():
    core, _manual = pp.load_prompt_tiers(PROMPT_PATH)
    for heading in (
        "HOW A TURN WORKS",
        "PRINCIPLES",
        "WHAT THIS SYSTEM CAN DO",
        "HOW TO USE YOUR CAPABILITIES",
        "CONTEXT BLOCKS AROUND THIS TEXT",
        "MEMORY DOCTRINE",
        "SAFETY AND REVERSIBILITY",
        "AUTONOMY AND SELF-EVOLUTION",
        "COMMUNICATION",
    ):
        assert f"\n{heading}\n" in f"\n{core}\n", f"{heading} must stay resident"
    # The big reference table is exactly what moved on demand.
    assert "OPERATING THE TOOLS" not in core


def test_manual_keeps_the_reference_material():
    _core, manual = pp.load_prompt_tiers(PROMPT_PATH)
    assert manual.startswith("TOOL SURFACE")
    # The header docs/skills-library-jit-on-demand-skills.md pins must survive.
    assert "SKILLS LIBRARY" in manual
    assert "OPERATING THE TOOLS (intent -> tool)" in manual


def test_split_prompt_is_robust_to_missing_marker_and_empty_core():
    whole, no_manual = pp.split_prompt("just instructions")
    assert whole == "just instructions" and no_manual == ""

    # A marker with nothing before it must not yield an empty system prompt.
    core, manual = pp.split_prompt("--- MANUAL ---\n\nonly the manual")
    assert core == "--- MANUAL ---\n\nonly the manual"
    assert manual == ""


def test_split_prompt_uses_the_marker_line_not_a_substring():
    text = "core line mentioning --- MANUAL --- inline\n\nkeep me\n\n--- MANUAL ---\n\nmanual"
    core, manual = pp.split_prompt(text)
    assert "inline" in core and manual == "manual"


def test_tier_for_defaults_to_the_lean_core():
    assert pp.tier_for({}) == pp.TIER_CORE
    assert pp.tier_for({"prompt_tier": "lite"}) == pp.TIER_CORE
    assert pp.tier_for({"prompt_tier": "nonsense"}) == pp.TIER_CORE


def test_tier_for_full_restores_the_combined_prompt():
    assert pp.tier_for({"prompt_tier": "full"}) == pp.TIER_FULL
    assert pp.tier_for({"prompt_tier": "combined"}) == pp.TIER_FULL


def test_tier_for_auto_reads_the_endpoint_location():
    assert pp.tier_for({"prompt_tier": "auto"}, "http://localhost:11434") == pp.TIER_CORE
    assert pp.tier_for({"prompt_tier": "auto"}, "http://127.0.0.1:8080") == pp.TIER_CORE
    assert pp.tier_for({"prompt_tier": "auto"}, "http://192.168.1.50:8000") == pp.TIER_FULL


def test_get_tool_surface_returns_the_manual_and_filters_sections():
    import asyncio

    from core import tool_bridge

    whole = asyncio.run(tool_bridge.execute_meta("get_tool_surface", {}))
    assert whole.startswith("TOOL SURFACE")
    assert "SKILLS LIBRARY" in whole

    only_skills = asyncio.run(
        tool_bridge.execute_meta("get_tool_surface", {"section": "skills"})
    )
    assert "SKILLS LIBRARY" in only_skills
    assert "TOOL SURFACE" not in only_skills

    missing = asyncio.run(
        tool_bridge.execute_meta("get_tool_surface", {"section": "no such section"})
    )
    assert "No manual section matches" in missing


def test_get_tool_surface_is_advertised_and_never_raises():
    import asyncio

    from core import tool_bridge, tool_index as ti

    assert ti.META_SURFACE_TOOL in ti.META_TOOLS
    names = {d["name"] for d in ti.META_DECLARATIONS}
    assert ti.META_SURFACE_TOOL in names
    assert tool_bridge.is_meta_tool(ti.META_SURFACE_TOOL)

    # Malformed arguments must come back as text, never as an exception.
    out = asyncio.run(
        tool_bridge.execute_meta("get_tool_surface", {"section": ["not", "a", "string"]})
    )
    assert isinstance(out, str) and out
