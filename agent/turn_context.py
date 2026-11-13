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

import datetime
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple

from . import second_brain
from .skills import discover as discover_skills
from .trace import redact_text

IntentType = Literal["conversational", "procedural", "interrogative", "mixed"]

DEFAULT_KNOWLEDGE_BUDGET = 2_400
DEFAULT_SKILL_BUDGET = 1_200
_TRUNCATION_MARKER = "\n… [truncated]"

# -- Semantic (T2) retrieval ------------------------------------------------
#
# T1 (trigger/domain/tag/description/name) is the contract: it always runs,
# needs no daemon and is fully reproducible from the record. T2 is an opt-in
# re-rank over a local vector table (``config/skills_embeddings.json``). The
# floor and weight mirror ``second_brain``'s split — its semantic path requires
# ``cosine >= 0.5`` while its lexical path has no such gate — and the weight is
# deliberately below the strongest single lexical signal (a 0.50 trigger hit),
# so a semantic match can lift a borderline skill but never outrank a declared
# trigger.
SKILL_COSINE_FLOOR = 0.5
SKILL_EMBED_WEIGHT = 0.35
SKILL_EMBED_ENV = "BRAHMA_SKILL_EMBED"
SKILL_INDEX_FILENAME = "skills_index.json"
SKILL_EMBEDDINGS_FILENAME = "skills_embeddings.json"

#: One `(stat_key, table)` entry; re-reading a rebuilt table needs no restart.
_EMBED_TABLE_CACHE: Dict[str, Tuple[Tuple[Any, ...], "SkillEmbeddingTable"]] = {}
_QUERY_VECTOR_CACHE: Dict[str, List[float]] = {}
_QUERY_VECTOR_CACHE_MAX = 64
_CACHE_LOCK = threading.Lock()

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
    #: True when the injected skill body was cut to fit ``skill_budget``.
    skill_truncated: bool = False
    #: Digest of the compiled index this plan was scored against, so a later
    #: audit can prove the record and the table drifted apart (rule R5).
    index_sha: str = ""
    #: Which retrieval lane decided the shortlist: "T1" keywords, "T2" local
    #: embeddings, "T3" model tie-break (not built yet).
    retrieval_tier: str = "T1"
    embed_model: Optional[str] = None
    embed_sha: Optional[str] = None
    #: Gap between the winner and the runner-up, in raw (unrounded) score.
    winner_margin: float = 0.0

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


@dataclass
class SkillEmbeddingTable:
    """A loaded ``config/skills_embeddings.json`` table (T2 retrieval)."""

    path: Optional[Path] = None
    vectors: Dict[str, List[float]] = field(default_factory=dict)
    model: Optional[str] = None
    digest: Optional[str] = None

    @property
    def available(self) -> bool:
        return bool(self.vectors) and self.path is not None


def _config_candidates(workspace: Optional[Path], filename: str) -> List[Path]:
    candidates: List[Path] = []
    if workspace:
        candidates.append(workspace / "config" / filename)
    candidates.extend([
        Path(__file__).resolve().parent.parent / "config" / filename,
        Path.cwd() / "config" / filename,
    ])
    return candidates


def skills_index_path(workspace: Optional[Path] = None) -> Optional[Path]:
    """Return the compiled skills index this turn would read, if any."""
    for cand in _config_candidates(workspace, SKILL_INDEX_FILENAME):
        if cand.is_file():
            return cand
    return None


def _file_digest(path: Optional[Path]) -> str:
    """sha256 of a build artefact, or ``""`` when it is absent/unreadable."""
    if path is None:
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _load_skills_index(workspace: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load the taxonomized skills index with cookbooks and triggers (§9.6, §9.10)."""
    path = skills_index_path(workspace)
    if path is None:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("skills"), list):
            return data["skills"]
    except Exception:
        pass
    return []


def indexed_skill_names(workspace: Optional[Path] = None) -> set[str]:
    """Lower-cased names in the compiled index — the set T1 can actually choose.

    Used by the model-load detector (design M5) to separate a routing *miss*
    (the skill was routable and was not chosen) from an ``off_index`` load (a
    project or Skills Library skill that T1 never sees).
    """
    return {
        str(item.get("name") or "").strip().lower()
        for item in _load_skills_index(workspace)
        if item.get("name")
    }


def load_skill_embeddings(workspace: Optional[Path] = None) -> SkillEmbeddingTable:
    """Load the optional local vector table, cached on ``(path, mtime, size)``.

    A rebuilt table is picked up without a restart, the same invalidation trick
    ``second_brain`` uses for its vector matrix. A missing or unreadable file
    yields an empty table, and every caller treats that as "T1 only".
    """
    path = None
    for cand in _config_candidates(workspace, SKILL_EMBEDDINGS_FILENAME):
        if cand.is_file():
            path = cand
            break
    if path is None:
        return SkillEmbeddingTable()
    try:
        stat = path.stat()
    except OSError:
        return SkillEmbeddingTable()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _CACHE_LOCK:
        cached = _EMBED_TABLE_CACHE.get("table")
    if cached is not None and cached[0] == key:
        return cached[1]

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return SkillEmbeddingTable()
    vectors: Dict[str, List[float]] = {}
    for row in data.get("skills") or []:
        name = str(row.get("name") or "")
        vector = row.get("vector")
        if name and isinstance(vector, list) and vector:
            vectors[name] = [float(value) for value in vector]
    table = SkillEmbeddingTable(
        path=path,
        vectors=vectors,
        model=str(data.get("model") or "") or None,
        digest=_file_digest(path),
    )
    with _CACHE_LOCK:
        _EMBED_TABLE_CACHE.clear()
        _EMBED_TABLE_CACHE["table"] = (key, table)
    return table


def _embedding_vector_for(
    query: str,
    embed: Callable[[str], Optional[Sequence[float]]],
) -> Optional[List[float]]:
    """One query vector per distinct query; a dead embedder answers ``None``."""
    key = hashlib.sha1(query.encode("utf-8", "replace")).hexdigest()
    with _CACHE_LOCK:
        cached = _QUERY_VECTOR_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        raw = embed(query)
    except Exception:
        return None
    if not raw:
        return None
    vector = [float(value) for value in raw]
    with _CACHE_LOCK:
        if len(_QUERY_VECTOR_CACHE) >= _QUERY_VECTOR_CACHE_MAX:
            _QUERY_VECTOR_CACHE.pop(next(iter(_QUERY_VECTOR_CACHE)))
        _QUERY_VECTOR_CACHE[key] = vector
    return vector


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Pure-Python cosine. 90 rows do not justify a numpy dependency."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = sum(a * a for a in left) ** 0.5
    norm_right = sum(b * b for b in right) ** 0.5
    if not norm_left or not norm_right:
        return 0.0
    return dot / (norm_left * norm_right)


def skills_embed_enabled() -> bool:
    """T2 is opt-in: ``BRAHMA_SKILL_EMBED=1``."""
    return os.environ.get(SKILL_EMBED_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _why_labels(
    signals: Dict[str, float],
    triggers_hit: Sequence[str],
    domain: str,
    tags_hit: Sequence[str],
) -> List[str]:
    """Human-readable account of *which* signals fired, not just the total."""
    why: List[str] = []
    for trigger in triggers_hit[:3]:
        why.append(f"trigger:{trigger}")
    if signals.get("domain"):
        why.append(f"domain:{domain}" or "domain")
    for tag in tags_hit[:2]:
        why.append(f"tag:{tag}")
    if signals.get("name"):
        why.append("name")
    if signals.get("description") and not why:
        why.append("description")
    if signals.get("embedding"):
        why.append(f"embedding:{signals['embedding']:.2f}")
    return why[:6]


def shortlist_skills(
    query: str,
    workspace: Optional[Path] = None,
    domain: Optional[str] = None,
    limit: int = 3,
    *,
    embed: Optional[Callable[[str], Optional[Sequence[float]]]] = None,
) -> List[Dict[str, Any]]:
    """Shortlist skills matching query and academic domain (§9.6, §9.10).

    Every entry carries a per-signal breakdown (``signals``) and a human-readable
    ``why`` list, so a later audit can explain the choice without re-running it.
    ``embed`` is an optional local embedder (T2): it may only *re-rank* the
    lexical score, never replace it, and an unanswered or failed call leaves T1
    in force.
    """
    ws = workspace or Path.cwd()

    # 1. Prefer compiled skills index if available in workspace or default config
    indexed_skills: List[Dict[str, Any]] = []
    if workspace and (workspace / "config" / SKILL_INDEX_FILENAME).is_file():
        indexed_skills = _load_skills_index(workspace)
    elif not workspace or ws == Path(__file__).resolve().parent.parent:
        indexed_skills = _load_skills_index()

    q_lower = query.lower()
    tokens = set(re.findall(r"[a-z0-9_-]{3,}", q_lower))

    table = SkillEmbeddingTable()
    query_vector: Optional[List[float]] = None
    if embed is not None and indexed_skills:
        table = load_skill_embeddings(workspace)
        if table.available:
            query_vector = _embedding_vector_for(query, embed)

    if indexed_skills:
        scored_indexed: List[Tuple[float, Dict[str, Any]]] = []
        for sk in indexed_skills:
            name = str(sk.get("name") or "")
            name_lower = name.lower()
            desc = str(sk.get("description") or "").lower()
            sk_domain = str(sk.get("academic_domain") or sk.get("domain") or "").lower()
            tags = [str(t).lower() for t in sk.get("tags") or []]
            triggers = [str(tr).lower() for tr in sk.get("triggers") or []]

            signals = {
                "trigger": 0.0,
                "domain": 0.0,
                "tags": 0.0,
                "description": 0.0,
                "name": 0.0,
                "embedding": 0.0,
            }

            # Trigger exact phrase matching (highest precision signal)
            triggers_hit = [tr for tr in triggers if tr and tr in q_lower]
            if triggers_hit:
                signals["trigger"] = 0.50

            if domain and sk_domain and (domain in sk_domain or sk_domain in domain):
                signals["domain"] = 0.40

            tags_hit = [t for t in tags if t in tokens]
            signals["tags"] = 0.15 * len(tags_hit)
            if any(t in desc for t in tokens):
                signals["description"] = 0.20
            if any(t in name_lower for t in tokens):
                signals["name"] = 0.35

            cosine: Optional[float] = None
            if query_vector is not None:
                vector = table.vectors.get(name)
                if vector:
                    cosine = max(-1.0, min(1.0, _cosine(query_vector, vector)))
                    if cosine >= SKILL_COSINE_FLOOR:
                        signals["embedding"] = round(SKILL_EMBED_WEIGHT * cosine, 4)

            score = sum(signals.values())
            if score < 0.25:
                continue

            scored_indexed.append((
                score,
                {
                    "name": name,
                    "description": sk.get("description", ""),
                    "body": sk.get("cookbook") or "",
                    "cookbook": sk.get("cookbook") or "",
                    "score": round(score, 2),
                    "score_raw": round(score, 4),
                    "allowed_tools": [],
                    "domain": sk.get("domain", ""),
                    "academic_domain": str(sk.get("academic_domain") or ""),
                    "tags": [str(t) for t in (sk.get("tags") or [])][:6],
                    "sha256": str(sk.get("sha256") or ""),
                    "cookbook_sha256": str(sk.get("cookbook_sha256") or ""),
                    "signals": {key: round(value, 4) for key, value in signals.items()},
                    "why": _why_labels(signals, triggers_hit, sk_domain, tags_hit),
                    "cosine": None if cosine is None else round(cosine, 4),
                },
            ))

        if scored_indexed:
            scored_indexed.sort(key=lambda s: s[0], reverse=True)
            return [entry for _, entry in scored_indexed[:limit]]

    # 2. Fall back to filesystem discovery via discover_skills
    discovered = discover_skills(ws)
    scored: List[Tuple[float, Any]] = []

    for sk in discovered.skills:
        name = sk.name.lower()
        desc = sk.description.lower()
        meta = sk.metadata or {}
        sk_domain = str(meta.get("domain") or meta.get("taxonomy_node") or "").lower()
        tags = [str(t).lower() for t in meta.get("tags") or []]

        signals = {"domain": 0.0, "tags": 0.0, "description": 0.0, "name": 0.0}
        if domain and sk_domain and (domain in sk_domain or sk_domain in domain):
            signals["domain"] = 0.45
        tags_hit = [t for t in tags if t in tokens]
        signals["tags"] = 0.15 * len(tags_hit)
        if any(t in desc for t in tokens):
            signals["description"] = 0.25
        if any(t in name for t in tokens):
            signals["name"] = 0.35

        score = sum(signals.values())
        if score < 0.25:
            continue
        scored.append((score, {
            "name": sk.name,
            "description": sk.description,
            "body": sk.body,
            "score": round(score, 2),
            "score_raw": round(score, 4),
            "allowed_tools": sk.allowed_tools,
            "domain": str(meta.get("domain") or ""),
            "academic_domain": str(meta.get("domain") or meta.get("taxonomy_node") or ""),
            "tags": [str(t) for t in (meta.get("tags") or [])][:6],
            "sha256": "",
            "cookbook_sha256": "",
            "signals": {key: round(value, 4) for key, value in signals.items()},
            "why": _why_labels(signals, [], sk_domain, tags_hit),
            "cosine": None,
        }))

    scored.sort(key=lambda s: s[0], reverse=True)
    return [entry for _, entry in scored[:limit]]


def compile_turn_context(
    query: str,
    *,
    workspace: Optional[Path] = None,
    knowledge_budget: int = DEFAULT_KNOWLEDGE_BUDGET,
    skill_budget: int = DEFAULT_SKILL_BUDGET,
    include_skills: bool = True,
    embed: Optional[Callable[[str], Optional[Sequence[float]]]] = None,
) -> TurnPlan:
    """Compile the full TurnPlan, rendering knowledge and skill blocks (§10.1).

    ``embed`` is an injected local embedder (T2). It is never imported here —
    ``agent/`` must import with only pytest installed — and when it is absent
    the plan is scored by T1 keywords alone.
    """
    text = " ".join(str(query or "").strip().split())
    plan = TurnPlan(
        query=text,
        knowledge_budget=knowledge_budget,
        skill_budget=skill_budget,
    )

    plan.intent = classify_intent(text)
    if plan.intent == "conversational":
        return plan

    # Pin the digest of the table this plan is scored against so a later audit
    # can tell a wrong pick apart from a stale index (rule R5).
    plan.index_sha = _file_digest(skills_index_path(workspace))

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
            active_embed = embed
            if active_embed is None and skills_embed_enabled():
                active_embed = second_brain.embed_query
            skills = shortlist_skills(
                text, workspace=workspace, domain=plan.domain, limit=3, embed=active_embed
            )
            plan.shortlisted_skills = skills
            if skills:
                top_skill = skills[0]
                plan.selected_skill = top_skill
                if len(skills) > 1:
                    plan.winner_margin = round(
                        float(skills[0].get("score_raw", skills[0].get("score", 0.0)))
                        - float(skills[1].get("score_raw", skills[1].get("score", 0.0))),
                        4,
                    )
                if any(entry.get("cosine") is not None for entry in skills):
                    plan.retrieval_tier = "T2"
                    table = load_skill_embeddings(workspace)
                    plan.embed_model = table.model
                    plan.embed_sha = table.digest
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
                        plan.skill_truncated = True
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
            plan.skill_truncated = True

    return plan


# -- Selection record (design §7) -------------------------------------------

#: Bounds that keep one record a few kilobytes, not a transcript.
RECORD_SHORTLIST_LIMIT = 3
RECORD_QUERY_CHARS = 2_000
RECORD_TOOLS_LIMIT = 24


def _cookbook_tools_used(hints: Sequence[str], tools_run: Sequence[str]) -> bool:
    if not hints:
        return False
    return bool(set(hints) & set(tools_run))


def selection_record(
    plan: TurnPlan,
    *,
    trace_id: str = "",
    session_id: Optional[str] = None,
    turn: int = 0,
    query: str = "",
    retain_query: bool = True,
    status: str = "completed",
    stop_reason: Optional[str] = None,
    failure_label: Optional[str] = None,
    tools_run: Optional[Sequence[str]] = None,
    verify: Optional[Sequence[str]] = None,
    cookbook_tools: Optional[Sequence[str]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Build the persisted skill-selection record.

    The shape mirrors :class:`~agent.trace.TraceRecord` so the two join on
    ``trace_id``. ``outcome`` is what makes the end-of-task detector a pure
    function over the file: nothing here needs the model, the workspace or a
    second run to be interpretable.
    """
    timestamp = float(now if now is not None else time.time())
    selected = plan.selected_skill or {}
    tools = [str(name) for name in (tools_run or [])][:RECORD_TOOLS_LIMIT]
    hints = [str(name) for name in (cookbook_tools or [])][:8]

    shortlist: List[Dict[str, Any]] = []
    for entry in (plan.shortlisted_skills or [])[:RECORD_SHORTLIST_LIMIT]:
        shortlist.append({
            "name": str(entry.get("name") or ""),
            "score": round(float(entry.get("score") or 0.0), 4),
            "score_raw": round(float(entry.get("score_raw", entry.get("score") or 0.0)), 4),
            "domain": str(entry.get("domain") or ""),
            "academic_domain": str(entry.get("academic_domain") or ""),
            "tags": list(entry.get("tags") or [])[:6],
            "why": list(entry.get("why") or [])[:6],
            "signals": dict(entry.get("signals") or {}),
            "cosine": entry.get("cosine"),
        })

    record: Dict[str, Any] = {
        "schema": 1,
        "ts": datetime.datetime.fromtimestamp(
            timestamp, datetime.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ts_epoch": round(timestamp, 3),
        "trace_id": trace_id,
        "session_id": session_id,
        "turn": int(turn or 0),
        "query_sha256": hashlib.sha256(
            str(query or "").encode("utf-8", "replace")
        ).hexdigest(),
        "intent": plan.intent,
        "domain": plan.domain or "",
        "domain_confidence": round(float(plan.domain_confidence or 0.0), 4),
        "retrieval": {
            "tier": plan.retrieval_tier,
            "embed_model": plan.embed_model,
            "embed_sha": plan.embed_sha,
        },
        "shortlist": shortlist,
        "selected": str(selected.get("name") or ""),
        "selected_sha": str(selected.get("sha256") or ""),
        "cookbook_sha": str(selected.get("cookbook_sha256") or ""),
        "selected_domain": str(selected.get("academic_domain") or selected.get("domain") or ""),
        "selected_tags": list(selected.get("tags") or [])[:6],
        "cookbook_tools": hints,
        "injected_chars": len(plan.skill_block or ""),
        "truncated": bool(plan.skill_truncated),
        "index_sha": plan.index_sha,
        "winner_margin": round(float(plan.winner_margin or 0.0), 4),
        "outcome": {
            "status": status,
            "stop_reason": stop_reason or "",
            "failure_label": failure_label,
            "tools_run": tools,
            "verify": [str(item) for item in (verify or [])][:6],
            "cookbook_tools_used": _cookbook_tools_used(hints, tools),
        },
        "verdict": None,
    }
    if retain_query:
        record["query"] = redact_text(str(query or "")[:RECORD_QUERY_CHARS])
    return record
