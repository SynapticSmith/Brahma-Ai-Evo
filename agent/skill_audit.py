"""Deterministic audit of skill-routing decisions (design §5, §6, §9).

This module is a *pure function over a recorded decision*. It never calls a
model, never re-scores a query, and never writes to ``skills/``. It answers one
question per task: was the skill we injected the right one for the work that
actually happened?

Design record: ``docs/SKILL-SELECTION-SELF-CHECK-LOOP.md``.

Rule order is R1..R5, with one deliberate exception: **R5 (source drift) is
evaluated first**. If ``skills/<n>/SKILL.md`` no longer matches the digest the
record was scored against, the record describes a skill the repository no
longer has, so a behavioural verdict would be untrustworthy. R5 therefore acts
as a precondition and names any other matching rules as evidence.

Standard-library only: no PyQt, no provider SDK, no COM. CI collects this module
with only pytest installed.
"""
from __future__ import annotations

import datetime
import difflib
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA = 1

# -- Rule and fix identifiers ----------------------------------------------

R1 = "R1"
R2 = "R2"
R3 = "R3"
R4 = "R4"
R5 = "R5"

KIND_WRONG = "wrong"
KIND_AMBIGUOUS = "ambiguous"
KIND_BLOAT = "bloat"
KIND_STALE = "stale"

FIX_RETARGET = "retarget"
FIX_DISAMBIGUATE = "disambiguate_pair"
FIX_DEMOTE = "demote_skill"
FIX_RETARGET_RUNNER_UP = "retarget_runner_up"
FIX_REBUILD = "rebuild_index"
FIX_MISSING_TRIGGER = "missing_trigger"

#: Failures that can plausibly be the skill's fault, not the model's.
_FAILURE_LABELS = frozenset({
    "tool_choice_error",
    "argument_error",
    "unverified_stop",
    "unknown_error",
    "skill_choice_error",
})

#: Score at or below this means the winner took the slot "by noise": the
#: shortlist cutoff is 0.25 and the weakest meaningful lexical hit is ~0.35.
_WEAK_SCORE = 0.30
#: Two candidates closer than this in another domain are not distinguishable.
_AMBIGUOUS_MARGIN = 0.10
#: Below this winner margin the runner-up is a legitimate alternative (R4).
_RUNNER_UP_MARGIN = 0.15

#: Domain vocabulary. ``academic_domain`` is free text from frontmatter and the
#: task side is a taxonomy node path, so both are mapped to a family before
#: comparison. An unmapped value yields no verdict rather than a guess.
_DOMAIN_FAMILIES: Dict[str, frozenset] = {
    "software": frozenset({
        "coding", "computing", "software", "programming", "program", "code",
        "engineering", "sciences", "applied", "computer",
    }),
    "devops": frozenset({
        "devops", "docker", "deploy", "deployment", "infrastructure", "ci",
        "git", "release", "operations", "ops", "kubernetes", "containers",
    }),
    "security": frozenset({
        "security", "vulnerability", "vuln", "audit", "secret", "secrets",
        "appsec", "exploit", "privilege", "escalation",
    }),
    "cognitive": frozenset({
        "cognitive", "reasoning", "thinking", "mental", "bias", "decision",
    }),
    "research": frozenset({
        "research", "knowledge", "scrape", "scraper", "extraction", "book",
        "taxonomy", "notes",
    }),
    "architecture": frozenset({
        "architecture", "scaffold", "structure", "layout", "refactor",
    }),
    "multimodal": frozenset({
        "multimodal", "media", "video", "audio", "image", "vision", "pdf",
    }),
    "methodology": frozenset({
        "methodology", "process", "workflow", "planning", "review",
    }),
}

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.+#-]{1,}")


# -- Verdicts ---------------------------------------------------------------


@dataclass
class Verdict:
    """One rule's judgement about one recorded decision."""

    rule: str
    kind: str
    why: str
    fix_kind: str
    diagnosed_by: str = "rule"
    candidates: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule,
            "kind": self.kind,
            "why": self.why,
            "fix_kind": self.fix_kind,
            "diagnosed_by": self.diagnosed_by,
            "candidates": list(self.candidates),
            "evidence": list(self.evidence),
        }


def _score(entry: Dict[str, Any]) -> float:
    if "score_raw" in entry:
        try:
            return float(entry.get("score_raw") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(entry.get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _domain_family(value: Any) -> Optional[str]:
    """Map a taxonomy path or a frontmatter domain onto a coarse family."""
    tokens = {token for token in _TOKEN_RE.findall(str(value or "").lower())}
    if not tokens:
        return None
    for family, vocabulary in _DOMAIN_FAMILIES.items():
        if tokens & vocabulary:
            return family
    return None


def _entry_domain(entry: Dict[str, Any]) -> str:
    return str(entry.get("academic_domain") or entry.get("domain") or "")


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _skill_md(skills_dir: Path, name: str) -> Optional[Path]:
    if not name:
        return None
    candidate = Path(skills_dir) / name / "SKILL.md"
    if candidate.is_file():
        return candidate
    # A skill directory may differ from its frontmatter name.
    lowered = name.lower()
    for path in Path(skills_dir).glob("*/SKILL.md"):
        if path.parent.name.lower() == lowered:
            return path
    return None


# -- Rules ------------------------------------------------------------------


def rule_drift(record: Dict[str, Any], skills_dir: Optional[Path]) -> Optional[Verdict]:
    """R5 — the skill source moved since the index the record was scored on."""
    if not skills_dir:
        return None
    selected = str(record.get("selected") or "")
    if not selected:
        return None
    skill_md = _skill_md(Path(skills_dir), selected)
    if skill_md is None:
        return None

    recorded = str(record.get("selected_sha") or "")
    current = _sha256_file(skill_md)
    if recorded and current and recorded != current:
        return Verdict(
            R5,
            KIND_STALE,
            f"{selected!r} SKILL.md changed after this record "
            f"({recorded[:12]} -> {current[:12]})",
            FIX_REBUILD,
            candidates=[selected],
        )

    # The index digest covers SKILL.md only, so a cookbook-only edit would be
    # invisible precisely in the half of the skill the model executes.
    recorded_cookbook = str(record.get("cookbook_sha") or "")
    cookbook = skill_md.parent / "cookbook.md"
    if recorded_cookbook and cookbook.is_file():
        try:
            text = cookbook.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None
        current_cookbook = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if current_cookbook != recorded_cookbook:
            return Verdict(
                R5,
                KIND_STALE,
                f"{selected!r} cookbook.md changed after this record "
                f"({recorded_cookbook[:12]} -> {current_cookbook[:12]})",
                FIX_REBUILD,
                candidates=[selected],
            )
    return None


def rule_domain_mismatch(record: Dict[str, Any]) -> Optional[Verdict]:
    """R1 — the task's taxonomy domain and the skill's domain disagree."""
    task_domain = str(record.get("domain") or "")
    try:
        confidence = float(record.get("domain_confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if not task_domain or confidence < 0.5:
        return None

    selected = str(record.get("selected") or "")
    skill_domain = str(record.get("selected_domain") or "")
    if not selected or not skill_domain:
        return None

    task_family = _domain_family(task_domain)
    skill_family = _domain_family(skill_domain)
    if task_family is None or skill_family is None or task_family == skill_family:
        return None

    return Verdict(
        R1,
        KIND_WRONG,
        f"task domain {task_domain!r} reads as {task_family!r} but the injected "
        f"skill {selected!r} declares {skill_domain!r} ({skill_family!r})",
        FIX_RETARGET,
        candidates=[selected],
    )


def rule_weak_signal(record: Dict[str, Any]) -> Optional[Verdict]:
    """R2 — the winner scored barely above the cutoff, with a rival next to it."""
    shortlist = record.get("shortlist") or []
    if not shortlist:
        return None
    top = shortlist[0]
    top_score = _score(top)
    if top_score > _WEAK_SCORE:
        return None

    top_name = str(top.get("name") or "")
    top_family = _domain_family(_entry_domain(top))
    for other in shortlist[1:]:
        other_family = _domain_family(_entry_domain(other))
        if top_family is not None and other_family is not None:
            if top_family == other_family:
                continue
        elif not _entry_domain(top) or not _entry_domain(other):
            continue
        other_score = _score(other)
        if abs(top_score - other_score) > _AMBIGUOUS_MARGIN:
            continue
        other_name = str(other.get("name") or "")
        return Verdict(
            R2,
            KIND_AMBIGUOUS,
            f"{top_name!r} won at {top_score:.2f} (cutoff 0.25) with "
            f"{other_name!r} at {other_score:.2f} in another domain",
            FIX_DISAMBIGUATE,
            candidates=[top_name, other_name],
        )
    return None


def rule_unused(record: Dict[str, Any]) -> Optional[Verdict]:
    """R3 — injected, task succeeded, and the cookbook's tools never ran.

    Not "wrong": expensive. This is the context-bloat KPI the whole
    progressive-disclosure design exists to move down.
    """
    outcome = record.get("outcome") or {}
    if str(outcome.get("status") or "") != "completed":
        return None
    hints = [str(item) for item in (record.get("cookbook_tools") or [])]
    if not hints:
        return None
    tools = [str(item) for item in (outcome.get("tools_run") or [])]
    if set(hints) & set(tools):
        return None

    selected = str(record.get("selected") or "")
    if not selected:
        return None
    if tools:
        why = (
            f"{selected!r} was injected but none of its cookbook tools "
            f"({', '.join(hints[:3])}) ran; the run used {', '.join(tools[:3])}"
        )
    else:
        why = f"{selected!r} was injected but no tool ran at all"
    return Verdict(R3, KIND_BLOAT, why, FIX_DEMOTE, candidates=[selected])


def rule_failure_attributable(record: Dict[str, Any]) -> Optional[Verdict]:
    """R4 — the run failed, and a runner-up fits the task domain better."""
    outcome = record.get("outcome") or {}
    label = str(outcome.get("failure_label") or "")
    if label not in _FAILURE_LABELS:
        return None
    selected = str(record.get("selected") or "")
    if not selected:
        return None

    task_domain = record.get("domain") or ""
    task_family = _domain_family(task_domain)
    try:
        margin = float(record.get("winner_margin") or 0.0)
    except (TypeError, ValueError):
        margin = 0.0
    close_call = margin <= _RUNNER_UP_MARGIN

    for entry in (record.get("shortlist") or [])[1:]:
        name = str(entry.get("name") or "")
        if not name or name == selected:
            continue
        family = _domain_family(_entry_domain(entry))
        domain_fits = task_family is not None and family is not None and family == task_family
        if not (domain_fits or close_call):
            continue
        reason = (
            "matches the task domain" if domain_fits
            else f"was only {margin:.2f} behind"
        )
        return Verdict(
            R4,
            KIND_WRONG,
            f"run failed with {label!r} after injecting {selected!r}; "
            f"{name!r} {reason}",
            FIX_RETARGET_RUNNER_UP,
            candidates=[name],
        )
    return None


#: Evaluation order. R5 is a precondition (see the module docstring).
_RULES: Tuple[Tuple[str, str], ...] = (
    (R5, "drift"),
    (R1, "domain_mismatch"),
    (R2, "weak_signal"),
    (R3, "unused"),
    (R4, "failure_attributable"),
)


def audit_record(
    record: Dict[str, Any],
    *,
    skills_dir: Optional[Path] = None,
) -> Optional[Verdict]:
    """Apply R1-R5 to one record. First match is the verdict; the rest is evidence."""
    if not isinstance(record, dict):
        return None
    if not str(record.get("selected") or ""):
        return None

    checks = {
        R5: lambda: rule_drift(record, skills_dir),
        R1: lambda: rule_domain_mismatch(record),
        R2: lambda: rule_weak_signal(record),
        R3: lambda: rule_unused(record),
        R4: lambda: rule_failure_attributable(record),
    }

    verdict: Optional[Verdict] = None
    for rule, _label in _RULES:
        matched = checks[rule]()
        if matched is None:
            continue
        if verdict is None:
            verdict = matched
        else:
            verdict.evidence.append(
                f"{matched.rule}:{matched.kind} — {matched.why}"
            )
    return verdict


def audit_records(
    records: Sequence[Dict[str, Any]],
    *,
    skills_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Audit a corpus, returning one finding per record that matched a rule."""
    findings: List[Dict[str, Any]] = []
    for record in records:
        verdict = audit_record(record, skills_dir=skills_dir)
        if verdict is None:
            continue
        findings.append({
            "trace_id": str(record.get("trace_id") or ""),
            "ts": str(record.get("ts") or ""),
            "ts_epoch": float(record.get("ts_epoch") or 0.0),
            "query": str(record.get("query") or ""),
            "selected": str(record.get("selected") or ""),
            "domain": str(record.get("domain") or ""),
            "index_sha": str(record.get("index_sha") or ""),
            "retrieval_tier": str((record.get("retrieval") or {}).get("tier") or "T1"),
            "injected_chars": int(record.get("injected_chars") or 0),
            "verdict": verdict.to_dict(),
            "record": record,
        })
    findings.sort(key=lambda item: item["ts_epoch"])
    return findings


# -- Metrics (design §9) ----------------------------------------------------


def summarize(
    records: Sequence[Dict[str, Any]],
    findings: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """The §9 scorecard, computed from the corpus, not estimated."""
    total = len(records)
    by_rule: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}
    for finding in findings:
        verdict = finding.get("verdict") or {}
        by_rule[verdict.get("rule", "?")] = by_rule.get(verdict.get("rule", "?"), 0) + 1
        by_kind[verdict.get("kind", "?")] = by_kind.get(verdict.get("kind", "?"), 0) + 1

    tiers: Dict[str, int] = {"T1": 0, "T2": 0, "T3": 0}
    chars = 0
    for record in records:
        tier = str((record.get("retrieval") or {}).get("tier") or "T1")
        tiers[tier] = tiers.get(tier, 0) + 1
        chars += int(record.get("injected_chars") or 0)

    judged_wrong = by_kind.get(KIND_WRONG, 0)
    return {
        "records": total,
        "findings": len(findings),
        "correct": max(0, total - len(findings)),
        "selection_precision": round((total - len(findings)) / total, 4) if total else 0.0,
        "wrong_pick_rate": round(judged_wrong / total, 4) if total else 0.0,
        "unused_rate": round(by_kind.get(KIND_BLOAT, 0) / total, 4) if total else 0.0,
        "ambiguous_rate": round(by_kind.get(KIND_AMBIGUOUS, 0) / total, 4) if total else 0.0,
        "by_rule": dict(sorted(by_rule.items())),
        "by_kind": dict(sorted(by_kind.items())),
        "retrieval_tier_mix": tiers,
        "prompt_chars_per_turn": round(chars / total, 1) if total else 0.0,
    }


def parse_since(value: Optional[str], now: Optional[float] = None) -> Optional[float]:
    """Parse ``7d``/``24h``/``90m`` or an ISO date into epoch seconds."""
    text = str(value or "").strip().lower()
    if not text or text == "all":
        return None
    current = float(now if now is not None else time.time())
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([mhdw])", text)
    if match:
        amount = float(match.group(1))
        unit = match.group(2)
        factor = {"m": 60, "h": 3_600, "d": 86_400, "w": 604_800}[unit]
        return current - amount * factor
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


# -- Staged fixes (design §6) -----------------------------------------------


_STOPWORDS = frozenset("""
a an the and or but if then than that this these those to of in on for with
from by as at is are was were be been being do does did doing have has had
i we you he she it they me us them my our your its please can could should
would will just also into about over under after before when where which who
""".split())


def _distinctive_tokens(query: str, skill_text: str, limit: int = 3) -> List[str]:
    """Words the task used that the skill does not already mention."""
    known = set(_TOKEN_RE.findall(skill_text.lower()))
    tokens = {
        token
        for token in _TOKEN_RE.findall(query.lower())
        if token not in known and token not in _STOPWORDS and len(token) >= 4
    }
    return sorted(tokens)[:limit]


def _add_triggers(text: str, triggers: Sequence[str]) -> str:
    """Insert trigger phrases into the frontmatter block, preserving the rest."""
    if not triggers:
        return text
    lines = text.splitlines()
    quoted = [f'  - "{item}"' for item in triggers]
    trailing = "\n" if text.endswith("\n") else ""

    close = None
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                close = index
                break

    if close is None:
        # A skill without frontmatter would not be indexed at all; write one.
        block = ["---", "triggers:"] + quoted + ["---", ""]
        return "\n".join(block + lines) + trailing

    trigger_line = None
    last_item = None
    for index in range(1, close):
        stripped = lines[index].strip()
        if trigger_line is None:
            if stripped.startswith("triggers:"):
                trigger_line = index
                inline = stripped[len("triggers:"):].strip()
                if inline.startswith("[") and inline.endswith("]"):
                    existing = [
                        part.strip().strip("\"'")
                        for part in inline[1:-1].split(",")
                        if part.strip()
                    ]
                    merged = existing + [item for item in triggers if item not in existing]
                    body = ", ".join(f'"{item}"' for item in merged)
                    lines[index] = f"triggers: [{body}]"
                    return "\n".join(lines) + trailing
        elif stripped.startswith("- "):
            last_item = index

    if trigger_line is not None:
        insert_at = (last_item if last_item is not None else trigger_line) + 1
        lines[insert_at:insert_at] = quoted
        return "\n".join(lines) + trailing

    insert_at = 1
    for index in range(1, close):
        if lines[index].strip().startswith("description:"):
            insert_at = index + 1
            break
    lines[insert_at:insert_at] = ["triggers:"] + quoted
    return "\n".join(lines) + trailing


def draft_fix(
    finding: Dict[str, Any],
    *,
    skills_dir: Optional[Path],
) -> Dict[str, Any]:
    """Turn a finding into a reviewable proposal. Never mutates ``skills/``.

    Returns a dict with ``kind`` (the fix kind), ``target`` (skill name),
    ``patch`` (a unified diff, possibly empty) and ``note``. A rebuild fix has
    no patch: the artefact is a command, not an edit.
    """
    verdict = finding.get("verdict") or {}
    fix_kind = str(verdict.get("fix_kind") or "")
    record = finding.get("record") or {}
    primary = (
        (verdict.get("candidates") or [None])[0]
        or str(record.get("selected") or "")
    )

    if fix_kind == FIX_REBUILD:
        return {
            "kind": fix_kind,
            "target": primary,
            "patch": "",
            "note": "rebuild the index: python tools/skills_index.py --build",
        }
    if not skills_dir or not primary:
        return {"kind": fix_kind, "target": primary, "patch": "", "note": "no source available"}

    skill_md = _skill_md(Path(skills_dir), primary)
    if skill_md is None:
        return {
            "kind": fix_kind,
            "target": primary,
            "patch": "",
            "note": f"skills/{primary}/SKILL.md not found; the skill may be missing",
        }

    try:
        original = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"kind": fix_kind, "target": primary, "patch": "", "note": "unreadable source"}

    if fix_kind in (FIX_RETARGET, FIX_DISAMBIGUATE):
        phrases = _distinctive_tokens(str(record.get("query") or ""), original)
        if not phrases:
            return {
                "kind": FIX_MISSING_TRIGGER,
                "target": primary,
                "patch": "",
                "note": "no distinctive query token survived; review the description by hand",
            }
        updated = _add_triggers(original, phrases)
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/skills/{skill_md.parent.name}/SKILL.md",
                tofile=f"b/skills/{skill_md.parent.name}/SKILL.md",
            )
        )
        return {
            "kind": FIX_MISSING_TRIGGER,
            "target": primary,
            "patch": diff,
            "note": f"proposed triggers: {', '.join(phrases)}",
            "proposed_triggers": phrases,
        }

    return {
        "kind": fix_kind,
        "target": primary,
        "patch": "",
        "note": "manual review required: demotion is a judgement call, not a token edit",
    }


def _staged_root() -> Path:
    try:
        from core.skill_forge import staged_skills_dir

        return staged_skills_dir() / "_audit"
    except Exception:  # noqa: BLE001 - core/ may be absent in a bare harness run
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) / "BrahmaAI" if local else Path(".brahma")
        return base / "staged_skills" / "_audit"


def stage_fix(
    finding: Dict[str, Any],
    *,
    skills_dir: Optional[Path],
    staged_root: Optional[Path] = None,
    stamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Write a proposal under ``staged_skills/_audit/`` for human review.

    Nothing is applied and nothing under ``skills/`` is touched: a skill body is
    prompt input, i.e. an injection surface, so promotion stays a human action
    (``agent/teacher_policy.py``: "Skills are NEVER auto-installed").
    """
    proposal = draft_fix(finding, skills_dir=skills_dir)
    verdict = finding.get("verdict") or {}
    label = stamp or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_target = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(proposal.get("target") or "unknown"))
    root = Path(staged_root) if staged_root is not None else _staged_root()
    out_dir = root / f"{label}_{verdict.get('rule', 'RX')}_{safe_target}"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "proposal.json").write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "generated": label,
                "trace_id": finding.get("trace_id", ""),
                "selected": finding.get("selected", ""),
                "verdict": verdict,
                "fix": proposal,
            },
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    if proposal.get("patch"):
        (out_dir / "proposal.patch").write_text(proposal["patch"], encoding="utf-8")

    readme = [
        f"# Skill routing fix — {verdict.get('rule', 'RX')} {verdict.get('kind', '')}",
        "",
        f"- rule: `{verdict.get('rule', '')}` — {verdict.get('why', '')}",
        f"- skill: `{proposal.get('target', '')}`",
        f"- fix kind: `{proposal.get('kind', '')}`",
        f"- trace: `{finding.get('trace_id', '')}`",
        "",
        "## What was observed",
        "",
        f"Query: {finding.get('query', '') or '(not retained)'}",
        "",
        f"Chosen: `{finding.get('selected', '')}` (domain: {finding.get('domain', '') or 'n/a'})",
        "",
        "## Proposed change",
        "",
        proposal.get("note", ""),
        "",
        "Target file: `skills/%s/SKILL.md`" % proposal.get("target", ""),
        "",
        "Review the patch, apply it by hand, then rebuild:",
        "",
        "```powershell",
        "python tools/skills_index.py --build",
        "```",
        "",
        "Nothing here is applied automatically.",
    ]
    (out_dir / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")

    return {
        "path": str(out_dir),
        "patch": proposal.get("patch", ""),
        "kind": proposal.get("kind", ""),
        "target": proposal.get("target", ""),
        "note": proposal.get("note", ""),
    }
