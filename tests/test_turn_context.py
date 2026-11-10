"""Tests for the Turn Context Compiler and JIT Skills Engine (§9, §10, Phase 6)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from agent.context import ContextBudget, ConversationContext
from agent.loop import AgentLoop
from agent.second_brain import with_turn_context
from agent.turn_context import (
    TurnPlan,
    classify_intent,
    compile_turn_context,
    shortlist_scripts,
    shortlist_skills,
)


def test_classify_intent_deterministic():
    # Conversational / trivial
    assert classify_intent("hello") == "conversational"
    assert classify_intent("thanks!") == "conversational"
    assert classify_intent("ok") == "conversational"
    assert classify_intent("") == "conversational"

    # Procedural
    assert classify_intent("build a web scraper for wikipedia") == "procedural"
    assert classify_intent("refactor the authentication module") == "procedural"
    assert classify_intent("implement binary search algorithm") == "procedural"

    # Interrogative
    assert classify_intent("what is quantum entanglement?") == "interrogative"
    assert classify_intent("explain how transformers work") == "interrogative"
    assert classify_intent("tell me about the history of buddhism") == "interrogative"

    # Mixed
    assert classify_intent("explain how quicksort works and write an implementation in python") == "mixed"
    assert classify_intent("what is oauth2 and implement a client") == "mixed"


def test_scripts_catalogue_shortlisting(tmp_path: Path):
    # Test script shortlisting with domain matching and keyword tokens
    results = shortlist_scripts("convert docx document to markdown", domain="applied_sciences/computing")
    assert len(results) >= 1
    top = results[0]
    assert "ingest_multimodal" in top["name"]
    assert top.get("safety") in ("read_only", "sandbox")

    # Test taxonomy tool
    tax_results = shortlist_scripts("lint notes in taxonomy", domain="applied_sciences/computing")
    assert any("taxonomy" in s["name"] for s in tax_results)


def test_skills_shortlisting_and_budget_clamping(tmp_path: Path):
    # Create a mock skill directory
    skills_dir = tmp_path / "skills" / "sample-skill"
    skills_dir.mkdir(parents=True)
    skill_content = """---
name: sample-skill
description: A sample procedural skill for testing.
metadata:
  domain: applied_sciences/computing
  tags: [sample, testing, procedural]
---
# Sample Procedure
Step 1: Inspect the target.
Step 2: Execute the routine.
Step 3: Verify the output.
"""
    (skills_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")

    shortlisted = shortlist_skills("run a sample testing procedural routine", workspace=tmp_path, domain="applied_sciences/computing")
    assert len(shortlisted) >= 1
    first = shortlisted[0]
    assert first["name"] == "sample-skill"
    assert "Step 1" in first["body"]

    # Compile turn context with strict skill budget (e.g. 50 chars)
    plan = compile_turn_context(
        "run a sample testing procedural routine",
        workspace=tmp_path,
        skill_budget=50,
        include_skills=True,
    )
    assert plan.has_skill
    assert "[SAMPLE-SKILL]" in plan.skill_block
    assert "… [truncated]" in plan.skill_block


def test_context_as_payload_ordering():
    ctx = ConversationContext(budget=ContextBudget(max_chars=10_000))
    ctx.append({"role": "system", "content": "BASE SYSTEM PROMPT"})
    ctx.append({"role": "user", "content": "First turn"})
    ctx.append({"role": "assistant", "content": "First response"})
    ctx.append({"role": "user", "content": "Second turn"})

    ctx.set_retrieval("KNOWLEDGE NOTE BLOCK")
    ctx.set_skill("TASK SKILL SOP BLOCK")

    payload = ctx.as_payload()

    # Must have 6 messages:
    # 0: base system prompt
    # 1: user first
    # 2: assistant first
    # 3: retrieval system message
    # 4: skill system message
    # 5: user second
    assert len(payload) == 6
    assert payload[0]["content"] == "BASE SYSTEM PROMPT"
    assert payload[1]["content"] == "First turn"
    assert payload[2]["content"] == "First response"
    assert payload[3]["role"] == "system" and payload[3]["content"] == "KNOWLEDGE NOTE BLOCK"
    assert payload[4]["role"] == "system" and payload[4]["content"] == "TASK SKILL SOP BLOCK"
    assert payload[5]["role"] == "user" and payload[5]["content"] == "Second turn"


def test_with_turn_context():
    messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "How does knowledge extraction work?"},
    ]
    copied, kb, sb = with_turn_context(messages, "How does knowledge extraction work?")
    # Check that messages was copied and not mutated in place
    assert len(messages) == 2
    # At least knowledge or skill injected before last user
    assert copied[-1]["role"] == "user"
    assert copied[-1]["content"] == "How does knowledge extraction work?"
    # The original messages structure is preserved in order
    assert copied[0]["role"] == "system"


def test_agent_loop_wires_skill_telemetry(monkeypatch):
    events: List[Dict[str, Any]] = []

    def mock_complete(messages: List[Dict[str, Any]], tools: Any = None) -> Dict[str, Any]:
        return {"role": "assistant", "content": "Mock done."}

    # Custom plan returned by compile_turn_context
    def mock_compile(*args, **kwargs):
        p = TurnPlan(query="test", intent="procedural", domain="formal_sciences/mathematics")
        p.knowledge_block = "SECOND BRAIN TEST NOTE"
        p.skill_block = "TASK SKILL TEST SOP"
        p.selected_skill = {"name": "test-math-skill"}
        p.selected_script = {"name": "test_script.py"}
        return p

    monkeypatch.setattr("agent.turn_context.compile_turn_context", mock_compile)

    loop = AgentLoop(
        complete=mock_complete,
        tools=[],
        on_event=events.append,
        retrieve=True,
    )
    result = loop.run("test turn")
    assert result.ok

    # Verify skill_injected event was emitted
    skill_events = [e for e in events if e.get("type") == "skill_injected"]
    assert len(skill_events) == 1
    evt = skill_events[0]
    assert evt["skill"] == "test-math-skill"
    assert evt["script"] == "test_script.py"
    assert evt["domain"] == "formal_sciences/mathematics"
    assert evt["chars"] == len("TASK SKILL TEST SOP")
