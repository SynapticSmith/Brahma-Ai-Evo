"""Turn Context Compiler and JIT Skills Engine (§9, §10).

Compiles a unified :class:`TurnPlan` before a turn reaches the model:
1. Classifies intent (conversational, procedural, interrogative, mixed).
2. Maps request to academic taxonomy domain (§8.5).
3. Renders bounded knowledge block from the user's Obsidian second brain.
4. Shortlists and injects at most one task skill SOP within character budget (§9.6, §9.10).
5. Cross-references verified repository scripts (§9.8, §9.9).

The result is injected before the user's turn as:
    [system prompt]
    [SECOND BRAIN — notes retrieved for this turn ...]
    [TASK SKILL — procedural guidance ...]
    [user turn]
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from . import second_brain
from .skills import discover as discover_skills

IntentType = Literal["conversational", "procedural", "interrogative", "mixed"]

DEFAULT_KNOWLEDGE_BUDGET = 2_400
DEFAULT_SKILL_BUDGET = 1_200
_TRUNCATION_MARKER = "\n… [truncated]"

_PROCEDURAL_VERBS = frozenset(
    """build create write make code implement refactor fix debug run test execute
    convert extract ingest migrate deploy setup install configure update clean
    split merge format compile lint audit verify commit push clone pull benchmark
    generate summarize parse translate patch analyze compute calculate inspect""".split()
)

_INTERROGATIVE_WORDS = frozenset(
    """what why how who when where explain describe define compare difference
    versus is are can could does do should would tell overview summary meaning""".split()
)


@dataclass
class TurnPlan:
    """The compiled execution plan and bounded context blocks for one turn (§4.1, §10.1)."""

    query: str
    intent: IntentType = "interrogative"
    domain: Optional[str] = None
    domain_confidence: float = 0.0
    knowledge_budget: int = DEFAULT_KNOWLEDGE_BUDGET
    skill_budget: int = DEFAULT_SKILL_BUDGET
    shortlisted_skills: List[Dict[str, Any]] = field(default_factory=list)
    selected_skill: Optional[Dict[str, Any]] = None
    shortlisted_scripts: List[Dict[str, Any]] = field(default_factory=list)
    selected_script: Optional[Dict[str, Any]] = None
    knowledge_block: str = ""
    skill_block: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def has_knowledge(self) -> bool:
        return bool(self.knowledge_block.strip())

    @property
    def has_skill(self) -> bool:
        return bool(self.skill_block.strip())


def classify_intent(text: str) -> IntentType:
    """Deterministic, cheap classification of turn intent (§10.2)."""
    cleaned = " ".join(str(text or "").strip().lower().split())
    if not cleaned:
        return "conversational"

    unpunct = cleaned.strip("!?,.:;'\"")
    if unpunct in second_brain._SKIP_EXACT:
        return "conversational"

    tokens = [t for t in re.findall(r"[a-z0-9_-]{2,}", cleaned)]
    if not tokens:
        return "conversational"

    has_procedural = any(token in _PROCEDURAL_VERBS for token in tokens)
    has_interrogative = any(token in _INTERROGATIVE_WORDS for token in tokens)

    if has_procedural and has_interrogative:
        return "mixed"
    if has_procedural:
        return "procedural"
    if has_interrogative:
        return "interrogative"

    if second_brain._SKIP_PREFIX.match(cleaned):
        return "procedural"

    return "interrogative"


def _load_scripts_catalogue() -> List[Dict[str, Any]]:
    """Load the verified scripts catalogue (§9.8)."""
    candidates = [
        Path(__file__).resolve().parent.parent / "config" / "scripts.json",
        Path(__file__).resolve().parent.parent / "scripts" / "index.json",
    ]
    for cand in candidates:
        if cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except Exception:
                pass
    return []


def shortlist_scripts(
    query: str,
    domain: Optional[str] = None,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Match query and domain against the verified scripts index (§9.9)."""
    scripts = _load_scripts_catalogue()
    if not scripts:
        return []

    tokens = set(re.findall(r"[a-z0-9_-]{3,}", query.lower()))
    scored: List[Tuple[float, Dict[str, Any]]] = []

    for item in scripts:
        name = str(item.get("name") or "").lower()
        desc = str(item.get("description") or "").lower()
        script_domain = str(item.get("domain") or "").lower()
        tags = [str(t).lower() for t in item.get("tags") or []]

        score = 0.0
        if domain and script_domain and (domain.startswith(script_domain) or script_domain.startswith(domain)):
            score += 0.40

        score += sum(0.15 for t in tags if t in tokens)
        if any(w in desc for w in tokens):
            score += 0.20
        if any(w in name for w in tokens):
            score += 0.35

        if score >= 0.25:
            scored.append((score, item))

    scored.sort(key=lambda s: s[0], reverse=True)
    return [item for _, item in scored[:limit]]


def _load_skills_index(workspace: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load the taxonomized skills index with cookbooks and triggers (§9.6, §9.10)."""
    candidates = []
    if workspace:
        candidates.append(workspace / "config" / "skills_index.json")
    candidates.extend([
        Path(__file__).resolve().parent.parent / "config" / "skills_index.json",
        Path.cwd() / "config" / "skills_index.json",
    ])
    for cand in candidates:
        if cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("skills"), list):
                    return data["skills"]
            except Exception:
                pass
    return []


def shortlist_skills(
    query: str,
    workspace: Optional[Path] = None,
    domain: Optional[str] = None,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Shortlist skills matching query and academic domain (§9.6, §9.10)."""
    ws = workspace or Path.cwd()

    # 1. Prefer compiled skills index if available in workspace or default config
    indexed_skills: List[Dict[str, Any]] = []
    if workspace and (workspace / "config" / "skills_index.json").is_file():
        indexed_skills = _load_skills_index(workspace)
    elif not workspace or ws == Path(__file__).resolve().parent.parent:
        indexed_skills = _load_skills_index()

    q_lower = query.lower()
    tokens = set(re.findall(r"[a-z0-9_-]{3,}", q_lower))

    if indexed_skills:
        scored_indexed: List[Tuple[float, Dict[str, Any]]] = []
        for sk in indexed_skills:
            score = 0.0
            name = str(sk.get("name") or "").lower()
            desc = str(sk.get("description") or "").lower()
            sk_domain = str(sk.get("academic_domain") or sk.get("domain") or "").lower()
            tags = [str(t).lower() for t in sk.get("tags") or []]
            triggers = [str(tr).lower() for tr in sk.get("triggers") or []]

            # Trigger exact phrase matching (highest precision signal)
            if any(tr in q_lower for tr in triggers):
                score += 0.50

            if domain and sk_domain and (domain in sk_domain or sk_domain in domain):
                score += 0.40

            score += sum(0.15 for t in tags if t in tokens)
            if any(t in desc for t in tokens):
                score += 0.20
            if any(t in name for t in tokens):
                score += 0.35

            if score >= 0.25:
                scored_indexed.append((score, sk))

        if scored_indexed:
            scored_indexed.sort(key=lambda s: s[0], reverse=True)
            out: List[Dict[str, Any]] = []
            for sc, sk in scored_indexed[:limit]:
                out.append({
                    "name": sk["name"],
                    "description": sk.get("description", ""),
                    "body": sk.get("cookbook") or "",
                    "cookbook": sk.get("cookbook") or "",
                    "score": round(sc, 2),
                    "allowed_tools": [],
                    "domain": sk.get("domain", ""),
                })
            return out

    # 2. Fall back to filesystem discovery via discover_skills
    discovered = discover_skills(ws)
    scored: List[Tuple[float, Any]] = []

    for sk in discovered.skills:
        score = 0.0
        name = sk.name.lower()
        desc = sk.description.lower()
        meta = sk.metadata or {}
        sk_domain = str(meta.get("domain") or meta.get("taxonomy_node") or "").lower()
        tags = [str(t).lower() for t in meta.get("tags") or []]

        if domain and sk_domain and (domain in sk_domain or sk_domain in domain):
            score += 0.45

        score += sum(0.15 for t in tags if t in tokens)
        if any(t in desc for t in tokens):
            score += 0.25
        if any(t in name for t in tokens):
            score += 0.35

        if score >= 0.25:
            scored.append((score, sk))

    scored.sort(key=lambda s: s[0], reverse=True)
    out_fallback: List[Dict[str, Any]] = []
    for sc, sk in scored[:limit]:
        out_fallback.append({
            "name": sk.name,
            "description": sk.description,
            "body": sk.body,
            "score": round(sc, 2),
            "allowed_tools": sk.allowed_tools,
        })
    return out_fallback


def compile_turn_context(
    query: str,
    *,
    workspace: Optional[Path] = None,
    knowledge_budget: int = DEFAULT_KNOWLEDGE_BUDGET,
    skill_budget: int = DEFAULT_SKILL_BUDGET,
    include_skills: bool = True,
) -> TurnPlan:
    """Compile the full TurnPlan, rendering knowledge and skill blocks (§10.1)."""
    text = " ".join(str(query or "").strip().split())
    plan = TurnPlan(
        query=text,
        knowledge_budget=knowledge_budget,
        skill_budget=skill_budget,
    )

    plan.intent = classify_intent(text)
    if plan.intent == "conversational":
        return plan

    # Map query to academic taxonomy domain (§8.5)
    try:
        candidates = second_brain.locate_nodes(text, limit=1)
        if candidates and candidates[0].get("confidence", 0) >= 0.50:
            plan.domain = candidates[0].get("path")
            plan.domain_confidence = candidates[0].get("confidence", 0.0)
    except Exception:
        pass

    # Render knowledge block (SECOND BRAIN)
    try:
        plan.knowledge_block = second_brain.context_for_turn(text, budget=knowledge_budget)
    except Exception:
        plan.knowledge_block = ""

    # Shortlist & render skill SOP block (TASK SKILL)
    if include_skills:
        # Resolve the verified-script note first: it is part of the same
        # skill_budget. Appending it after the body was truncated let the
        # rendered block run past the budget by the header + note length.
        script_note = ""
        try:
            scripts = shortlist_scripts(text, domain=plan.domain, limit=1)
            plan.shortlisted_scripts = scripts
            if scripts:
                top_script = scripts[0]
                plan.selected_script = top_script
                script_note = (
                    f"[Verified Script: {top_script['name']} | Safety: {top_script.get('safety', 'read_only')}]\n"
                    f"Usage: python {top_script.get('path', top_script['name'])} (see {top_script.get('description', '')})"
                )
        except Exception:
            pass

        try:
            skills = shortlist_skills(text, workspace=workspace, domain=plan.domain, limit=3)
            plan.shortlisted_skills = skills
            if skills:
                top_skill = skills[0]
                plan.selected_skill = top_skill
                raw_body = str(top_skill.get("body") or "").strip()
                if raw_body:
                    header = "\n\n".join([
                        "TASK SKILL — procedural guidance for this turn "
                        "(advisory SOP; grants no execution permissions):",
                        f"[{top_skill['name'].upper()}] {top_skill.get('description', '')}",
                    ])
                    separator = "\n\n"
                    reserved = len(header) + len(separator)
                    if script_note:
                        reserved += len(script_note) + len(separator)
                    body_budget = max(0, skill_budget - reserved - len(_TRUNCATION_MARKER))
                    if len(raw_body) > body_budget:
                        raw_body = raw_body[:body_budget].rstrip() + _TRUNCATION_MARKER
                    plan.skill_block = header + separator + raw_body
                    if script_note:
                        plan.skill_block += separator + script_note
        except Exception:
            pass

        if not plan.skill_block and script_note and plan.intent in ("procedural", "mixed"):
            plan.skill_block = "TASK SCRIPT GUIDANCE:\n" + script_note

        # Hard cap: a budget too small to hold the header still must not leak
        # an oversized block into the request payload.
        if skill_budget <= 0:
            plan.skill_block = ""
        elif len(plan.skill_block) > skill_budget:
            keep = max(0, skill_budget - len(_TRUNCATION_MARKER))
            plan.skill_block = plan.skill_block[:keep].rstrip() + _TRUNCATION_MARKER

    return plan
