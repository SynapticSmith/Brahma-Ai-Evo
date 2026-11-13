"""Deterministic audit of skill-selection records (design §5, §6, §7, §9).

Standard-library plus pytest only: this module ships in the harness CI matrix,
which installs nothing else.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent.skill_audit import (
    FIX_DEMOTE,
    FIX_REBUILD,
    FIX_RETARGET_RUNNER_UP,
    KIND_AMBIGUOUS,
    KIND_BLOAT,
    KIND_STALE,
    KIND_WRONG,
    LOAD_CONFIRMED,
    LOAD_MISS,
    LOAD_OFF_INDEX,
    LOAD_UNROUTED,
    R1,
    R2,
    R3,
    R4,
    R5,
    audit_record,
    audit_records,
    classify_model_load,
    draft_fix,
    extract_model_loads,
    load_name_from_args,
    miss_digest,
    model_load_record,
    parse_since,
    rule_domain_mismatch,
    rule_drift,
    rule_failure_attributable,
    rule_unused,
    rule_weak_signal,
    stage_fix,
    summarize,
)
from agent.turn_context import TurnPlan, selection_record


def _entry(
    name: str,
    score: float,
    domain: str = "",
    *,
    why: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "score": round(score, 2),
        "score_raw": score,
        "domain": domain,
        "academic_domain": domain,
        "tags": [],
        "why": list(why or []),
        "signals": {},
        "cosine": None,
    }


def _record(**overrides) -> dict:
    base = {
        "schema": 1,
        "ts": "2026-11-12T10:00:00Z",
        "ts_epoch": 1762932000.0,
        "trace_id": "tr_test",
        "query": "do the thing",
        "intent": "procedural",
        "domain": "",
        "domain_confidence": 0.0,
        "retrieval": {"tier": "T1", "embed_model": None, "embed_sha": None},
        "shortlist": [_entry("alpha", 1.0, "coding")],
        "selected": "alpha",
        "selected_sha": "",
        "cookbook_sha": "",
        "selected_domain": "coding",
        "selected_tags": [],
        "cookbook_tools": [],
        "injected_chars": 800,
        "truncated": False,
        "index_sha": "a" * 64,
        "winner_margin": 0.5,
        "outcome": {
            "status": "completed",
            "stop_reason": "completed",
            "failure_label": None,
            "tools_run": ["read_file"],
            "verify": [],
            "cookbook_tools_used": False,
        },
        "verdict": None,
    }
    base.update(overrides)
    return base


# -- R5: source drift -------------------------------------------------------


def _write_skill(root: Path, name: str, text: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    skill = folder / "SKILL.md"
    skill.write_text(text, encoding="utf-8")
    return skill


def test_r5_flags_skill_md_drift(tmp_path: Path):
    root = tmp_path / "skills"
    skill = _write_skill(root, "alpha", "# alpha\nthe new body\n")
    old_sha = hashlib.sha256(b"the old body\n").hexdigest()

    verdict = rule_drift(_record(selected_sha=old_sha), root)
    assert verdict is not None
    assert verdict.rule == R5
    assert verdict.kind == KIND_STALE
    assert verdict.fix_kind == FIX_REBUILD
    assert verdict.candidates == ["alpha"]
    assert hashlib.sha256(skill.read_bytes()).hexdigest() != old_sha


def test_r5_flags_cookbook_drift_even_when_skill_md_matches(tmp_path: Path):
    root = tmp_path / "skills"
    skill = _write_skill(root, "alpha", "# alpha\n")
    cookbook = skill.parent / "cookbook.md"
    cookbook.write_text("1. new step\n", encoding="utf-8")

    matching_md = hashlib.sha256(skill.read_bytes()).hexdigest()
    verdict = rule_drift(
        _record(selected_sha=matching_md, cookbook_sha="0" * 64), root
    )
    assert verdict is not None
    assert verdict.rule == R5
    assert "cookbook.md" in verdict.why


def test_r5_is_silent_when_nothing_moved_or_source_is_absent(tmp_path: Path):
    root = tmp_path / "skills"
    skill = _write_skill(root, "alpha", "# alpha\n")
    matching = hashlib.sha256(skill.read_bytes()).hexdigest()

    assert rule_drift(_record(selected_sha=matching), root) is None
    assert rule_drift(_record(selected_sha=matching), None) is None
    # Unknown skill: no source to compare, so no drift claim.
    assert rule_drift(_record(selected="ghost", selected_sha="0" * 64), root) is None


def test_r5_is_evaluated_before_behavioural_rules(tmp_path: Path):
    root = tmp_path / "skills"
    _write_skill(root, "alpha", "# alpha\nchanged\n")
    record = _record(
        selected_sha="0" * 64,
        domain="security",
        domain_confidence=0.9,
        selected_domain="cognitive",
    )
    verdict = audit_record(record, skills_dir=root)
    assert verdict is not None
    assert verdict.rule == R5
    # The domain rule still matched; it is reported as corroborating evidence.
    assert any(item.startswith(R1) for item in verdict.evidence)


# -- R1: domain mismatch ----------------------------------------------------


def test_r1_flags_cross_family_domain():
    verdict = rule_domain_mismatch(
        _record(domain="security", domain_confidence=0.9, selected_domain="cognitive")
    )
    assert verdict is not None
    assert verdict.rule == R1
    assert verdict.kind == KIND_WRONG
    assert verdict.candidates == ["alpha"]


def test_r1_respects_confidence_and_family_equivalence():
    # Below the taxonomy confidence floor: no claim.
    assert (
        rule_domain_mismatch(
            _record(domain="security", domain_confidence=0.2, selected_domain="cognitive")
        )
        is None
    )
    # Different strings, same family.
    assert (
        rule_domain_mismatch(
            _record(
                domain="applied-sciences/computing/software-engineering",
                domain_confidence=0.9,
                selected_domain="coding",
            )
        )
        is None
    )
    # Unmapped vocabulary yields no verdict rather than a guess.
    assert (
        rule_domain_mismatch(
            _record(domain="interpretive-dance", domain_confidence=0.9, selected_domain="coding")
        )
        is None
    )


# -- R2: weak signal / ambiguity -------------------------------------------


def test_r2_flags_close_call_in_another_domain():
    record = _record(
        shortlist=[_entry("alpha", 0.28, "coding"), _entry("beta", 0.25, "cognitive")],
        selected="alpha",
        selected_domain="coding",
    )
    verdict = rule_weak_signal(record)
    assert verdict is not None
    assert verdict.rule == R2
    assert verdict.kind == KIND_AMBIGUOUS
    assert verdict.candidates == ["alpha", "beta"]


def test_r2_ignores_same_family_and_clear_winners():
    same_family = _record(
        shortlist=[_entry("alpha", 0.28, "coding"), _entry("beta", 0.25, "coding")],
        selected_domain="coding",
    )
    assert rule_weak_signal(same_family) is None

    clear = _record(
        shortlist=[_entry("alpha", 0.28, "coding"), _entry("beta", 0.10, "cognitive")],
        selected_domain="coding",
    )
    assert rule_weak_signal(clear) is None

    strong = _record(
        shortlist=[_entry("alpha", 1.05, "coding"), _entry("beta", 0.25, "cognitive")],
        selected_domain="coding",
    )
    assert rule_weak_signal(strong) is None


# -- R3: injected but unused ------------------------------------------------


def test_r3_flags_cookbook_that_never_ran():
    record = _record(
        cookbook_tools=["write_file", "read_file"],
        outcome={
            "status": "completed",
            "stop_reason": "completed",
            "failure_label": None,
            "tools_run": ["run_command"],
            "verify": [],
            "cookbook_tools_used": False,
        },
    )
    verdict = rule_unused(record)
    assert verdict is not None
    assert verdict.rule == R3
    assert verdict.kind == KIND_BLOAT
    assert verdict.fix_kind == FIX_DEMOTE


def test_r3_is_silent_when_the_run_failed_or_no_hints_exist():
    failed = _record(
        cookbook_tools=["write_file"],
        outcome={
            "status": "error",
            "stop_reason": "error",
            "failure_label": "tool_choice_error",
            "tools_run": [],
            "verify": [],
            "cookbook_tools_used": False,
        },
    )
    assert rule_unused(failed) is None
    assert rule_unused(_record(cookbook_tools=[])) is None


# -- R4: failure attributable to a runner-up --------------------------------


def test_r4_offers_runner_up_that_matches_the_task():
    record = _record(
        domain="security",
        domain_confidence=0.9,
        shortlist=[_entry("alpha", 1.0, "security"), _entry("beta", 0.8, "security")],
        selected="alpha",
        selected_domain="security",
        winner_margin=0.2,
        outcome={
            "status": "error",
            "stop_reason": "error",
            "failure_label": "tool_choice_error",
            "tools_run": ["read_file"],
            "verify": [],
            "cookbook_tools_used": True,
        },
    )
    verdict = rule_failure_attributable(record)
    assert verdict is not None
    assert verdict.rule == R4
    assert verdict.fix_kind == FIX_RETARGET_RUNNER_UP
    assert verdict.candidates == ["beta"]


def test_r4_uses_a_close_margin_when_domains_are_unknown():
    record = _record(
        shortlist=[_entry("alpha", 1.0), _entry("beta", 0.9)],
        winner_margin=0.1,
        outcome={
            "status": "error",
            "stop_reason": "error",
            "failure_label": "unverified_stop",
            "tools_run": [],
            "verify": [],
            "cookbook_tools_used": False,
        },
    )
    verdict = rule_failure_attributable(record)
    assert verdict is not None
    assert "only 0.10 behind" in verdict.why


def test_r4_ignores_success_and_unrelated_failure_labels():
    success = _record(
        shortlist=[_entry("alpha", 1.0), _entry("beta", 0.9)],
        winner_margin=0.1,
    )
    assert rule_failure_attributable(success) is None

    permission = _record(
        shortlist=[_entry("alpha", 1.0), _entry("beta", 0.9)],
        winner_margin=0.1,
        outcome={
            "status": "error",
            "stop_reason": "permission_denied",
            "failure_label": "permission_denied",
            "tools_run": [],
            "verify": [],
            "cookbook_tools_used": False,
        },
    )
    assert rule_failure_attributable(permission) is None


# -- Corpus: determinism, metrics, parse_since ------------------------------


def test_audit_records_is_deterministic_and_summarises(tmp_path: Path):
    root = tmp_path / "skills"
    _write_skill(root, "alpha", "# alpha\n")
    good = _record(trace_id="tr_good")
    bad = _record(
        trace_id="tr_bad",
        domain="security",
        domain_confidence=0.9,
        selected_domain="cognitive",
    )
    records = [bad, good]

    first = audit_records(records, skills_dir=root)
    second = audit_records(records, skills_dir=root)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert len(first) == 1
    assert first[0]["trace_id"] == "tr_bad"

    metrics = summarize(records, first)
    assert metrics["records"] == 2
    assert metrics["findings"] == 1
    assert metrics["selection_precision"] == 0.5
    assert metrics["wrong_pick_rate"] == 0.5
    assert metrics["by_rule"] == {R1: 1}
    assert metrics["retrieval_tier_mix"]["T1"] == 2


def test_audit_record_ignores_records_without_a_pick():
    assert audit_record({}) is None
    assert audit_record(_record(selected="")) is None


def test_parse_since_accepts_relative_and_iso_values():
    import datetime

    now = 1_800_000_000.0
    assert parse_since("all", now=now) is None
    assert parse_since("", now=now) is None
    assert parse_since("nonsense", now=now) is None
    assert parse_since("1d", now=now) == now - 86_400
    assert parse_since("2h", now=now) == now - 7_200
    iso = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat()
    parsed = parse_since(iso, now=0)
    assert parsed is not None and abs(parsed - now) < 0.001


# -- Staged fixes: never mutate skills/ ------------------------------------


def _finding_for(record: dict, *, fix_kind: str, candidates: list[str]) -> dict:
    return {
        "trace_id": record.get("trace_id", ""),
        "query": record.get("query", ""),
        "selected": record.get("selected", ""),
        "domain": record.get("domain", ""),
        "record": record,
        "verdict": {
            "rule": R1,
            "kind": KIND_WRONG,
            "why": "test finding",
            "fix_kind": fix_kind,
            "diagnosed_by": "rule",
            "candidates": candidates,
            "evidence": [],
        },
    }


def test_draft_fix_proposes_a_trigger_addition_as_a_diff(tmp_path: Path):
    root = tmp_path / "skills"
    skill = _write_skill(
        root,
        "alpha",
        '---\nname: alpha\ndescription: does alpha things\ndomain: coding\n---\n\n# Alpha\n',
    )
    original = skill.read_text(encoding="utf-8")
    record = _record(query="audit unverified terraform rollout drift", selected="alpha")

    proposal = draft_fix(
        _finding_for(record, fix_kind="retarget", candidates=["alpha"]),
        skills_dir=root,
    )
    assert proposal["kind"] == "missing_trigger"
    assert proposal["target"] == "alpha"
    assert "triggers:" in proposal["patch"]
    assert "a/skills/alpha/SKILL.md" in proposal["patch"]
    # The draft is a proposal, not an edit.
    assert skill.read_text(encoding="utf-8") == original


def test_stage_fix_writes_a_review_packet_and_never_touches_skills(tmp_path: Path):
    root = tmp_path / "skills"
    skill = _write_skill(
        root,
        "alpha",
        '---\nname: alpha\ndescription: does alpha things\ndomain: coding\n---\n\n# Alpha\n',
    )
    staged_root = tmp_path / "staged_skills" / "_audit"
    before = hashlib.sha256(skill.read_bytes()).hexdigest()
    record = _record(query="audit unverified terraform rollout drift", selected="alpha")

    result = stage_fix(
        _finding_for(record, fix_kind="retarget", candidates=["alpha"]),
        skills_dir=root,
        staged_root=staged_root,
        stamp="20261112T000000Z",
    )

    assert hashlib.sha256(skill.read_bytes()).hexdigest() == before
    out_dir = Path(result["path"])
    assert out_dir.is_dir()
    assert out_dir.parent == staged_root
    assert (out_dir / "proposal.json").is_file()
    assert (out_dir / "proposal.patch").is_file()
    assert "Nothing here is applied automatically" in (
        out_dir / "README.md"
    ).read_text(encoding="utf-8")
    payload = json.loads((out_dir / "proposal.json").read_text(encoding="utf-8"))
    assert payload["fix"]["target"] == "alpha"


def test_stage_fix_for_a_rebuild_is_a_command_not_a_patch(tmp_path: Path):
    record = _record(selected_sha="0" * 64)
    finding = _finding_for(record, fix_kind="rebuild_index", candidates=["alpha"])
    proposal = draft_fix(finding, skills_dir=None)
    assert proposal["kind"] == "rebuild_index"
    assert proposal["patch"] == ""
    assert "tools/skills_index.py --build" in proposal["note"]


# -- selection_record shape -------------------------------------------------


def test_selection_record_is_joinable_and_redacted():
    plan = TurnPlan(
        query="scan for secrets",
        intent="procedural",
        domain="security",
        domain_confidence=0.8,
        shortlisted_skills=[
            {
                "name": "secret-and-leak-scanner",
                "score": 1.65,
                "score_raw": 1.65,
                "domain": "security",
                "academic_domain": "security",
                "tags": ["secrets"],
                "why": ["trigger:scan for secrets"],
                "signals": {"trigger": 0.5},
                "cosine": None,
                "sha256": "s" * 64,
                "cookbook_sha256": "c" * 64,
            }
        ],
        selected_skill={
            "name": "secret-and-leak-scanner",
            "academic_domain": "security",
            "tags": ["secrets"],
            "sha256": "s" * 64,
            "cookbook_sha256": "c" * 64,
        },
        index_sha="i" * 64,
        winner_margin=0.6,
        skill_block="x" * 300,
        skill_truncated=True,
    )

    record = selection_record(
        plan,
        trace_id="tr_join",
        session_id="ses_1",
        turn=3,
        query="deploy password=hunter2 then scan for secrets",
        tools_run=["read_file", "secret_scan"],
        verify=["command:PASSED"],
        cookbook_tools=["secret_scan"],
        now=1_800_000_000.0,
    )

    assert record["schema"] == 1
    assert record["trace_id"] == "tr_join"
    assert record["session_id"] == "ses_1"
    assert record["selected"] == "secret-and-leak-scanner"
    assert record["selected_sha"] == "s" * 64
    assert record["cookbook_sha"] == "c" * 64
    assert record["index_sha"] == "i" * 64
    assert record["winner_margin"] == 0.6
    assert record["injected_chars"] == 300
    assert record["truncated"] is True
    assert record["outcome"]["cookbook_tools_used"] is True
    assert record["outcome"]["verify"] == ["command:PASSED"]
    assert record["verdict"] is None
    assert "hunter2" not in record["query"]
    assert record["query_sha256"]


def test_selection_record_can_omit_the_query_text():
    plan = TurnPlan(query="secret task", intent="procedural", selected_skill={"name": "alpha"})
    record = selection_record(plan, query="secret task", retain_query=False)
    assert "query" not in record
    assert record["query_sha256"]


# -- Model-initiated loads (M5) --------------------------------------------


def test_load_name_from_args_accepts_both_surfaces():
    assert load_name_from_args({"name": " alpha "}) == "alpha"
    assert load_name_from_args({"skill_id": "library.skill"}) == "library.skill"
    assert load_name_from_args({"skill": "beta"}) == "beta"
    assert load_name_from_args({}) == ""
    assert load_name_from_args(None) == ""


def test_extract_model_loads_reads_events_and_dedupes():
    events = [
        {"type": "tool_call", "name": "load_project_skill", "arguments": {"name": "alpha"}},
        {"type": "tool_call", "name": "read_file", "arguments": {"path": "x"}},
        {"type": "tool_call", "name": "load_skill", "arguments": {"skill_id": "beta"}},
        {"type": "tool_call", "name": "load_project_skill", "arguments": {"name": "alpha"}},
        {"type": "tool_result", "name": "load_project_skill"},
    ]
    assert extract_model_loads(events) == [
        {"tool": "load_project_skill", "name": "alpha"},
        {"tool": "load_skill", "name": "beta"},
    ]
    assert len(extract_model_loads(events, dedupe=False)) == 3


def test_classify_model_load_keeps_verdicts_honest():
    assert classify_model_load("alpha", injected="alpha", indexed=True)[0] == LOAD_CONFIRMED
    assert classify_model_load("beta", injected="alpha", indexed=True)[0] == LOAD_MISS
    # Nothing injected but the name is routable: still a miss.
    assert classify_model_load("beta", injected=None, indexed=True)[0] == LOAD_MISS
    # Not in the compiled index: another surface, so not the router's fault.
    off_index = classify_model_load("project-skill", injected="alpha", indexed=False)
    assert off_index[0] == LOAD_OFF_INDEX
    # A path with no router decision (voice) is usage, never a verdict.
    assert (
        classify_model_load("alpha", indexed=True, routed=False)[0] == LOAD_UNROUTED
    )
    assert (
        classify_model_load("thing", indexed=False, routed=False)[0] == LOAD_OFF_INDEX
    )


def test_miss_digest_counts_outcomes_and_ranks_names():
    records = [
        model_load_record(
            tool="load_project_skill", name="alpha", source="harness",
            injected="alpha", indexed=True, now=100.0,
        ),
        model_load_record(
            tool="load_project_skill", name="beta", source="harness",
            injected="alpha", indexed=True, now=200.0,
        ),
        model_load_record(
            tool="load_project_skill", name="beta", source="harness",
            injected="gamma", indexed=True, now=300.0,
        ),
        model_load_record(
            tool="load_skill", name="lib", source="voice", routed=False, now=400.0,
        ),
        model_load_record(
            tool="load_project_skill", name="delta", source="voice",
            routed=False, indexed=True, now=500.0,
        ),
    ]
    digest = miss_digest(records)
    assert digest["loads"] == 5
    assert digest["misses"] == 2
    assert digest["confirmed"] == 1
    assert digest["off_index"] == 1
    assert digest["unrouted"] == 1
    assert digest["by_skill"] == {"beta": 2}
    assert [item["name"] for item in digest["recent_misses"]] == ["beta", "beta"]
    assert digest["miss_rate"] == 0.4


def test_note_skill_load_writes_an_unrouted_voice_row(tmp_path, monkeypatch):
    from agent.skill_audit import note_skill_load
    from agent.trace import SkillLoadSink

    monkeypatch.setattr(
        "agent.turn_context.indexed_skill_names", lambda workspace=None: {"alpha"}
    )
    sink = SkillLoadSink(base_dir=tmp_path)

    record = note_skill_load("load_project_skill", {"name": "alpha"}, sink=sink)

    assert record is not None
    assert record["source"] == "voice"
    assert record["outcome"] == LOAD_UNROUTED
    assert record["injected"] is None
    assert [row["name"] for row in sink.list_records()] == ["alpha"]

    # A non-load tool is not an observation, and a broken sink is swallowed.
    assert note_skill_load("read_file", {"path": "x"}, sink=sink) is None

    class _Broken:
        def save(self, _record):
            raise OSError("disk full")

    assert note_skill_load("load_skill", {"name": "alpha"}, sink=_Broken()) is None
    assert len(sink.list_records()) == 1


def test_loop_records_a_load_even_when_nothing_was_injected(tmp_path, monkeypatch):
    from agent.loop import AgentLoop
    from agent.runtime import AgentRuntime
    from agent.trace import SkillLoadSink

    skill_root = tmp_path / "skills"
    _write_skill(skill_root, "alpha", "# alpha\n")
    monkeypatch.setenv("BRAHMA_SKILLS_DIR", str(skill_root))
    monkeypatch.setattr(
        "agent.turn_context._load_skills_index", lambda workspace=None: []
    )

    runtime = AgentRuntime(workspace=tmp_path)
    calls = {"n": 0}

    def completer(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "load_project_skill",
                            "arguments": json.dumps({"name": "alpha"}),
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "done"}

    sink = SkillLoadSink(base_dir=tmp_path / "telemetry")
    loop = AgentLoop(
        completer,
        runtime=runtime,
        max_turns=3,
        retrieve=False,
        include_skills=False,
        skill_load_log=sink,
    )
    result = loop.run("load the alpha skill")

    assert result.ok is True
    records = sink.list_records()
    assert len(records) == 1
    assert records[0]["name"] == "alpha"
    assert records[0]["injected"] is None
    assert records[0]["outcome"] == LOAD_OFF_INDEX
    assert records[0]["source"] == "harness"


def test_loop_classifies_against_the_runtime_workspace_index(tmp_path, monkeypatch):
    """A routable name loaded while nothing was injected is a miss, not off-index.

    Regression: the classifier once read the index relative to the process cwd,
    so a run in another workspace called every load off-index and hid the miss.
    """
    from agent.loop import AgentLoop
    from agent.runtime import AgentRuntime
    from agent.trace import SkillLoadSink

    skill_root = tmp_path / "skills"
    _write_skill(skill_root, "alpha", "# alpha\n")
    monkeypatch.setenv("BRAHMA_SKILLS_DIR", str(skill_root))

    seen = {}

    def fake_index(workspace=None):
        seen["workspace"] = workspace
        return [{"name": "alpha"}]

    monkeypatch.setattr("agent.turn_context._load_skills_index", fake_index)

    calls = {"n": 0}

    def completer(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "load_project_skill",
                            "arguments": json.dumps({"name": "alpha"}),
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "done"}

    sink = SkillLoadSink(base_dir=tmp_path / "telemetry")
    loop = AgentLoop(
        completer,
        runtime=AgentRuntime(workspace=tmp_path),
        max_turns=3,
        retrieve=False,
        include_skills=False,
        skill_load_log=sink,
    )
    result = loop.run("load the alpha skill")

    assert result.ok is True
    records = sink.list_records()
    assert [row["outcome"] for row in records] == [LOAD_MISS]
    assert records[0]["indexed"] is True
    assert Path(str(seen["workspace"])).resolve() == tmp_path.resolve()

