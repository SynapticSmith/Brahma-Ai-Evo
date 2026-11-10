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
    assert top.get("safety") in ("read_only", "sandbox", "workspace_write")

    # Test taxonomy tool
    tax_results = shortlist_scripts("lint notes in taxonomy", domain="applied_sciences/computing")
    assert any("taxonomy" in s["name"] for s in tax_results)


def test_skills_index_cognitive_triggers_and_cookbooks():
    # Test high-precision trigger matching on cognitive thinking skills
    premortem = shortlist_skills("we should perform a premortem analysis before running this migration")
    assert len(premortem) >= 1
    assert premortem[0]["name"] == "pre-mortem-analysis"
    assert "Cookbook: Pre-Mortem Risk Immunization" in premortem[0]["cookbook"]

    inversion = shortlist_skills("solve backwards and invert always invert")
    assert len(inversion) >= 1
    assert inversion[0]["name"] == "inversion-thinking"
    assert "Cookbook: Inversion-Based Problem Solving" in inversion[0]["cookbook"]

    occams = shortlist_skills("apply occams razor to cut unnecessary complexity")
    assert len(occams) >= 1
    assert occams[0]["name"] == "occams-razor"

    boundary = shortlist_skills("optimize context boundary and token budget")
    assert len(boundary) >= 1
    assert boundary[0]["name"] == "context-boundary-optimizer"


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


def test_absorbed_skills_triggers_and_cookbooks():
    """Verify triggers and cookbooks for skills absorbed from portfolio catalog."""
    # Systematic debugging
    debug_skills = shortlist_skills("we need systematic debugging to isolate bug and root cause")
    assert len(debug_skills) >= 1
    assert debug_skills[0]["name"] == "systematic-debugging"
    assert "Cookbook: Systematic Bug Isolation and Repair" in debug_skills[0]["cookbook"]

    # Spec-driven development
    spec_skills = shortlist_skills("apply spec driven development and write an sdd planning doc")
    assert len(spec_skills) >= 1
    assert spec_skills[0]["name"] == "spec-driven-development"

    # Chesterton's fence
    fence_skills = shortlist_skills("remember chestertons fence before deleting this legacy code")
    assert len(fence_skills) >= 1
    assert fence_skills[0]["name"] == "chestertons-fence"

    # Reversibility / Two-way doors
    door_skills = shortlist_skills("is this a one way door or a two way doors decision?")
    assert len(door_skills) >= 1
    assert door_skills[0]["name"] == "reversibility-two-way-doors"

    # Pareto principle
    pareto_skills = shortlist_skills("apply pareto principle 80 20 rule to find top bottlenecks")
    assert len(pareto_skills) >= 1
    assert pareto_skills[0]["name"] == "pareto-principle-80-20"

    # Skill distillation
    distill_skills = shortlist_skills("distill a skill from this API documentation guide")
    assert len(distill_skills) >= 1
    assert distill_skills[0]["name"] == "skill-distillation"

    # HTML Answer / Dashboard
    html_skills = shortlist_skills("answer me with html report and single page dashboard")
    assert len(html_skills) >= 1
    assert html_skills[0]["name"] == "answer-me-with-html"

    # FFmpeg media processing
    media_skills = shortlist_skills("ffmpeg media processing to transcode video and normalize audio")
    assert len(media_skills) >= 1
    assert media_skills[0]["name"] == "ffmpeg-media-processing"

    # Code graph navigator
    graph_skills = shortlist_skills("code graph navigator to trace call hierarchy and find callers")
    assert len(graph_skills) >= 1
    assert graph_skills[0]["name"] == "code-graph-navigator"

    # Circle of competence
    competence_skills = shortlist_skills("circle of competence to know what you dont know")
    assert len(competence_skills) >= 1
    assert competence_skills[0]["name"] == "circle-of-competence"

    # Root-cause fishbone
    fishbone_skills = shortlist_skills("root cause fishbone ishikawa diagram for failure analysis")
    assert len(fishbone_skills) >= 1
    assert fishbone_skills[0]["name"] == "root-cause-fishbone"

    # Parkinson's law compression
    parkinson_skills = shortlist_skills("parkinsons law compression to force radical simplicity")
    assert len(parkinson_skills) >= 1
    assert parkinson_skills[0]["name"] == "parkinsons-law-compression"


def test_openmono_playbooks_triggers_and_cookbooks():
    """Verify triggers and cookbooks for playbooks absorbed from OpenMonoAgent."""
    # Incident response
    ir_skills = shortlist_skills("production is down and we must handle outage incident response")
    assert len(ir_skills) >= 1
    assert ir_skills[0]["name"] == "incident-response"
    assert "Incident Response" in ir_skills[0]["cookbook"]

    # Database migration ops
    db_skills = shortlist_skills("run db migration with safe database migration schema playbook")
    assert len(db_skills) >= 1
    assert db_skills[0]["name"] == "database-migration-ops"
    assert "Database Migration" in db_skills[0]["cookbook"]

    # Release engineering
    rel_skills = shortlist_skills("cut a release and cut new version with release engineering")
    assert len(rel_skills) >= 1
    assert rel_skills[0]["name"] == "release-engineering"
    assert "Release" in rel_skills[0]["cookbook"]

    # PR readiness
    pr_skills = shortlist_skills("check pr readiness and prepare pull request for review")
    assert len(pr_skills) >= 1
    assert pr_skills[0]["name"] == "pr-readiness"
    assert "PR Readiness" in pr_skills[0]["cookbook"]

    # Secret & leak scanner
    sec_skills = shortlist_skills("pre-commit security audit to scan for secrets and detect leaks")
    assert len(sec_skills) >= 1
    assert sec_skills[0]["name"] == "secret-and-leak-scanner"
    assert "Secret" in sec_skills[0]["cookbook"]

    # Conventional commit generator
    commit_skills = shortlist_skills("generate commit message with conventional commit for staged changes")
    assert len(commit_skills) >= 1
    assert commit_skills[0]["name"] == "conventional-commit-generator"
    assert "Conventional Commit" in commit_skills[0]["cookbook"]

    # Blast radius analyzer
    blast_skills = shortlist_skills("calculate blast radius and change impact analysis for this symbol")
    assert len(blast_skills) >= 1
    assert blast_skills[0]["name"] == "blast-radius-analyzer"
    assert "Blast Radius" in blast_skills[0]["cookbook"]


def test_batch4_skills_triggers_and_cookbooks():
    """Verify triggers and cookbooks for Batch 4 portfolio and OpenMono skills."""
    # Server deployment ops
    deploy_skills = shortlist_skills("server deployment ops to pull and deploy backend")
    assert len(deploy_skills) >= 1
    assert deploy_skills[0]["name"] == "server-deployment-ops"
    assert "Server Deployment" in deploy_skills[0]["cookbook"]

    # LLMs.txt generator
    llms_skills = shortlist_skills("generate llms.txt and create llms-full.txt documentation index")
    assert len(llms_skills) >= 1
    assert llms_skills[0]["name"] == "llms-txt-generator"
    assert "LLMs.txt" in llms_skills[0]["cookbook"]

    # Windows GUI automation
    gui_skills = shortlist_skills("windows gui automation to inspect window handle and click desktop element")
    assert len(gui_skills) >= 1
    assert gui_skills[0]["name"] == "windows-gui-automation"
    assert "Windows Desktop GUI" in gui_skills[0]["cookbook"]

    # Autonomous web research
    web_skills = shortlist_skills("autonomous web research to triangulate web sources and synthesize notes")
    assert len(web_skills) >= 1
    assert web_skills[0]["name"] == "autonomous-web-research"
    assert "Web Research" in web_skills[0]["cookbook"]

    # Scroll-driven web design
    scroll_skills = shortlist_skills("scroll driven web design with css animation timeline")
    assert len(scroll_skills) >= 1
    assert scroll_skills[0]["name"] == "scroll-driven-web-design"
    assert "Scroll-Driven" in scroll_skills[0]["cookbook"]

    # Grounded claim verification
    claim_skills = shortlist_skills("grounded claim verification and provenance check for codebase facts")
    assert len(claim_skills) >= 1
    assert claim_skills[0]["name"] == "grounded-claim-verification"
    assert "Provenance" in claim_skills[0]["cookbook"]

    # Desktop app instrumentation
    app_skills = shortlist_skills("desktop app instrumentation to intercept desktop app from python")
    assert len(app_skills) >= 1
    assert app_skills[0]["name"] == "desktop-app-instrumentation"
    assert "Desktop Process Interception" in app_skills[0]["cookbook"]

    # Postgres query optimizer
    sql_skills = shortlist_skills("postgres query optimizer to run explain analyze on slow sql query")
    assert len(sql_skills) >= 1
    assert sql_skills[0]["name"] == "postgres-query-optimizer"
    assert "PostgreSQL Query Plan" in sql_skills[0]["cookbook"]


def test_batch5_skills_triggers_and_cookbooks():
    """Verify triggers and cookbooks for Batch 5 skills (71-80)."""
    # Text to CAD modeling
    cad_skills = shortlist_skills("text to cad modeling to generate cad model and export stl model")
    assert len(cad_skills) >= 1
    assert cad_skills[0]["name"] == "text-to-cad-modeling"
    assert "OpenSCAD" in cad_skills[0]["cookbook"]

    # Book to skill compiler
    book_skills = shortlist_skills("compile book into skill with book to skill compiler")
    assert len(book_skills) >= 1
    assert book_skills[0]["name"] == "book-to-skill-compiler"
    assert "Book Distillation" in book_skills[0]["cookbook"]

    # Interactive data visualization
    viz_skills = shortlist_skills("interactive data visualization to generate interactive chart and create vega lite chart")
    assert len(viz_skills) >= 1
    assert viz_skills[0]["name"] == "interactive-data-visualization"
    assert "Vega-Lite" in viz_skills[0]["cookbook"]

    # Collaborative canvas designer
    canvas_skills = shortlist_skills("collaborative canvas designer to generate canvas wireframe and author visual mockup")
    assert len(canvas_skills) >= 1
    assert canvas_skills[0]["name"] == "collaborative-canvas-designer"
    assert "SVG Canvas" in canvas_skills[0]["cookbook"]

    # Multimodal media crawler
    crawler_skills = shortlist_skills("multimodal media crawler to extract video transcript and crawl social media post")
    assert len(crawler_skills) >= 1
    assert crawler_skills[0]["name"] == "multimodal-media-crawler"
    assert "Subtitle" in crawler_skills[0]["cookbook"]

    # SQLite FTS optimizer
    fts_skills = shortlist_skills("sqlite fts optimizer to optimize sqlite search and tune fts5 index")
    assert len(fts_skills) >= 1
    assert fts_skills[0]["name"] == "sqlite-fts-optimizer"
    assert "FTS5" in fts_skills[0]["cookbook"]

    # REST API contract generator
    api_skills = shortlist_skills("rest api contract generator to generate openapi spec and create swagger spec")
    assert len(api_skills) >= 1
    assert api_skills[0]["name"] == "rest-api-contract-generator"
    assert "OpenAPI" in api_skills[0]["cookbook"]

    # Circuit breaker resilience
    resilience_skills = shortlist_skills("circuit breaker resilience to implement circuit breaker and retry with exponential backoff")
    assert len(resilience_skills) >= 1
    assert resilience_skills[0]["name"] == "circuit-breaker-resilience"
    assert "Circuit Breaker" in resilience_skills[0]["cookbook"]

    # Zero downtime canary deploy
    canary_skills = shortlist_skills("zero downtime canary deploy and canary deployment with traffic split deploy")
    assert len(canary_skills) >= 1
    assert canary_skills[0]["name"] == "zero-downtime-canary-deploy"
    assert "Canary" in canary_skills[0]["cookbook"]

    # Epistemic arbitrage
    arb_skills = shortlist_skills("epistemic arbitrage to exploit information asymmetry and find information edge")
    assert len(arb_skills) >= 1
    assert arb_skills[0]["name"] == "epistemic-arbitrage"
    assert "Epistemic Arbitrage" in arb_skills[0]["cookbook"]


def test_batch6_skills_triggers_and_cookbooks():
    """Verify triggers and cookbooks for Batch 6 skills (81-90)."""
    # Smart web scraper graph
    scraper_skills = shortlist_skills("smart web scraper graph to extract json from web page and schema based scraper")
    assert len(scraper_skills) >= 1
    assert scraper_skills[0]["name"] == "smart-web-scraper-graph"
    assert "Schema-Driven" in scraper_skills[0]["cookbook"]

    # Graph RAG hybrid retriever
    graph_skills = shortlist_skills("graph rag hybrid retriever and hybrid graph retrieval with entity relation retrieval")
    assert len(graph_skills) >= 1
    assert graph_skills[0]["name"] == "graph-rag-hybrid-retriever"
    assert "Reciprocal Rank Fusion" in graph_skills[0]["cookbook"]

    # Structured grammar generator
    grammar_skills = shortlist_skills("structured grammar generator with constrained decoding and json schema constrained output")
    assert len(grammar_skills) >= 1
    assert grammar_skills[0]["name"] == "structured-grammar-generator"
    assert "Constrained JSON" in grammar_skills[0]["cookbook"]

    # Answering engine synthesizer
    answer_skills = shortlist_skills("answering engine synthesizer for perplexity style answer with citations")
    assert len(answer_skills) >= 1
    assert answer_skills[0]["name"] == "answering-engine-synthesizer"
    assert "Perplexity-Style" in answer_skills[0]["cookbook"]

    # Universal document converter
    converter_skills = shortlist_skills("universal document converter to convert document to markdown and pdf to clean markdown")
    assert len(converter_skills) >= 1
    assert converter_skills[0]["name"] == "universal-document-converter"
    assert "Multi-Format" in converter_skills[0]["cookbook"]

    # Test suite flakiness hunter
    hunter_skills = shortlist_skills("test suite flakiness hunter to fix flaky test and debug intermittent test failure")
    assert len(hunter_skills) >= 1
    assert hunter_skills[0]["name"] == "test-suite-flakiness-hunter"
    assert "Flaky Test" in hunter_skills[0]["cookbook"]

    # Sunk cost fallacy detector
    sunk_skills = shortlist_skills("sunk cost fallacy detector to identify sunk costs and decommission dead feature")
    assert len(sunk_skills) >= 1
    assert sunk_skills[0]["name"] == "sunk-cost-fallacy-detector"
    assert "Sunk Cost" in sunk_skills[0]["cookbook"]

    # Loss aversion calibrator
    loss_skills = shortlist_skills("loss aversion calibrator to calibrate loss aversion and overcome fear of change")
    assert len(loss_skills) >= 1
    assert loss_skills[0]["name"] == "loss-aversion-calibrator"
    assert "Loss Aversion" in loss_skills[0]["cookbook"]

    # Survivorship bias auditor
    survivor_skills = shortlist_skills("survivorship bias auditor to audit survivorship bias and analyze silent failures")
    assert len(survivor_skills) >= 1
    assert survivor_skills[0]["name"] == "survivorship-bias-auditor"
    assert "Survivorship Bias" in survivor_skills[0]["cookbook"]

    # Confirmation bias inversion
    falsify_skills = shortlist_skills("confirmation bias inversion to hunt for disconfirming evidence and popperian falsification")
    assert len(falsify_skills) >= 1
    assert falsify_skills[0]["name"] == "confirmation-bias-inversion"
    assert "Falsification" in falsify_skills[0]["cookbook"]




