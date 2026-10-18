"""
Standalone Skills Library for Brahma AI Evo.

What this is
------------
A managed, file-backed library of *skills* that is deliberately separate from
``features/`` and from the tool catalogue. A **skill** is a self-contained unit
of capability the agent can be handed only when it needs it:

* **Instructional skills** — pure context. They carry ``instructions`` and an
  optional longer ``manual``. Nothing is executable; the value is the guidance,
  which is injected on demand (never auto-injected into every turn).
* **Executable skills** — they ship an ``executor.py`` and can be invoked like
  any other tool once the agent has asked for them.

The design goal is the same one that produced the JIT tool index: the library
can hold **tens of thousands** of skills while the model's context carries
almost none of them. Only the lightweight catalogue (id, name, description,
tags, category, flags, path) is ever resident; full instruction bodies and
executor modules are materialised **on request**.

Relation to the rest of the system
----------------------------------
* :mod:`core.tool_index` — the *tool* catalogue and its ``search_tools`` /
  ``list_tools`` / ``call_tool`` / ``get_skill_manual`` discovery layer. This
  module is a peer, not a subclass: it has its own index, its own meta-tools
  (``search_skills`` / ``list_skills`` / ``load_skill``) and its own store.
* :mod:`core.dynamic_registry` — the execution surface. Executable skills are
  published into it as external tools, so they run through the exact same
  worker-thread dispatch as every other tool.
* :mod:`core.tool_bridge` — wires the library into the Live session and executes
  the three skill meta-tools.

On-disk format
--------------
A skill is either a **directory**::

    <root>/<dir>/
        skill.yaml      # or skill.yml / skill.json / SKILL.md
        executor.py     # optional; required when executable: true

or a **single file** (instructional only)::

    <root>/<name>.yaml | .yml | .json | .md

Two roots are scanned, in order, and a later root overrides an earlier one on
an id collision:

1. ``<repo>/skills_library`` — built-ins that ship with the codebase.
2. ``<user data>/skills_library`` — user skills, the editable/workable layer.

Fail-soft everywhere: a malformed skill is skipped with a warning and can never
stop the library (or the app) from loading.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import logging
import re
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from core.user_paths import get_user_data_dir

logger = logging.getLogger("SkillsLibrary")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Built-in skills that ship with the codebase.
BUILTIN_SKILLS_DIR = PROJECT_ROOT / "skills_library"

#: Recognised skill-definition filenames inside a skill directory, in priority order.
SKILL_FILENAMES: Tuple[str, ...] = ("skill.yaml", "skill.yml", "skill.json", "SKILL.md", "skill.md")

#: Extensions accepted for a single-file (instructional) skill.
SINGLE_FILE_SUFFIXES: Tuple[str, ...] = (".yaml", ".yml", ".json", ".md")

#: File stems that are documentation, never skills.
_IGNORED_STEMS = frozenset({"readme", "index", "changelog", "license", "skill"})

#: How many fully-materialised skills to keep in memory. The catalogue itself is
#: tiny; this bounds only the *bodies* and interpreter-held executor modules.
DEFAULT_CACHE_SIZE = 256

#: Source label used for skills registered into the tool catalogue.
SKILL_SOURCE = "skill_library"

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_VALID_NAME_CHARS = re.compile(r"[^0-9A-Za-z_]+")

#: Generic English filler with no routing signal, dropped before indexing.
_STOPWORDS = frozenset("""
a an and are as at be been being but by can could did do does doing done for
from had has have having he her here hers him his how i if in into is it its
just me my of on or our out over own please she should so some such than that
the their them then there these they this those to too us was we were what
when where which who whom why will with would you your
get got make made use used using want need like also any all more most some
skill skills feature features tool tools
""".split())


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass
class SkillSpec:
    """A fully-materialised skill (metadata plus body).

    Reading the body is deliberately explicit so the catalogue scan can stay
    metadata-only; see :meth:`SkillsLibrary.refresh`.
    """

    skill_id: str
    name: str = ""
    description: str = ""
    version: str = "1.0.0"
    category: str = "general"
    tags: List[str] = field(default_factory=list)
    active: bool = True
    executable: bool = False
    tool_name: str = ""
    parameters: Dict[str, Any] = field(default_factory=lambda: {"type": "OBJECT", "properties": {}})
    instructions: str = ""
    manual: str = ""
    dependencies: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    #: "builtin" | "user"
    source: str = "user"
    #: Directory (executable) or the single definition file itself.
    path: Optional[Path] = None
    #: Fingerprint of the definition file, for cache invalidation.
    checksum: str = ""

    @property
    def directory(self) -> Optional[Path]:
        if self.path is None:
            return None
        return self.path if self.path.is_dir() else self.path.parent

    def resolved_tool_name(self) -> str:
        """The name an executable skill is exposed under.

        Honours an explicit ``tool_name``; otherwise derives ``skill_<id>`` so a
        skill can never shadow a native feature by accident.
        """
        if self.tool_name:
            return sanitize_identifier(self.tool_name)
        return sanitize_identifier(f"skill_{self.skill_id}")

    def rendered(self, max_chars: int = 8000) -> str:
        """The on-demand block handed to the model by ``load_skill``."""
        title = self.name or self.skill_id
        body = (self.instructions or "").strip() or (self.description or "").strip()
        if len(body) > max_chars:
            body = body[:max_chars].rsplit(" ", 1)[0] + "\n… (truncated)"
        parts = [f"[SKILL: {title}]\n{body}"]
        manual = (self.manual or "").strip()
        if manual:
            if len(manual) > max_chars:
                manual = manual[:max_chars].rsplit(" ", 1)[0] + "\n… (truncated)"
            parts.append(f"[SKILL MANUAL: {title}]\n{manual}")
        if self.executable:
            props = (self.parameters or {}).get("properties") or {}
            argspec = ", ".join(sorted(props.keys())) if isinstance(props, dict) and props else "none"
            required = (self.parameters or {}).get("required")
            req = f" (required: {', '.join(required)})" if required else ""
            parts.append(
                "[CALLABLE]\n"
                f"This skill is executable as the tool '{self.resolved_tool_name()}'. "
                f"Run it with call_tool(tool_name='{self.resolved_tool_name()}', arguments={{...}}). "
                f"Arguments: {argspec}{req}."
            )
        return "\n\n".join(parts)


@dataclass
class SkillMeta:
    """Lightweight, always-resident catalogue row.

    Deliberately excludes ``instructions``, ``manual`` and ``parameters`` — that
    is what keeps a 10,000-skill library cheap to hold in memory.
    """

    skill_id: str
    name: str = ""
    description: str = ""
    category: str = "general"
    tags: List[str] = field(default_factory=list)
    executable: bool = False
    active: bool = True
    source: str = "user"
    tool_name: str = ""
    path: Optional[Path] = None
    version: str = "1.0.0"
    mtime: float = 0.0
    size: int = 0
    checksum: str = ""

    def name_tokens(self) -> set:
        return set(_tokens(f"{self.skill_id} {self.name}"))

    def tag_tokens(self) -> set:
        return set(_tokens(" ".join(self.tags)))

    def resolved_tool_name(self) -> str:
        """The tool name an executable skill is (or will be) exposed under."""
        if self.tool_name:
            return sanitize_identifier(self.tool_name)
        return sanitize_identifier(f"skill_{self.skill_id}")


@dataclass
class SkillHit:
    """One ranked skill-library result."""

    meta: SkillMeta
    score: float

    @property
    def skill_id(self) -> str:
        return self.meta.skill_id


@dataclass
class LibraryStats:
    """Diagnostics for the UI/logs. Never required for correctness."""

    skills: int = 0
    executable: int = 0
    active: int = 0
    disabled: int = 0
    builtin: int = 0
    user: int = 0
    cached: int = 0
    last_scan_ms: float = 0.0
    last_query_ms: float = 0.0
    enabled: bool = True


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #


def _tokens(text: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS and len(t) > 1]


def sanitize_identifier(value: str) -> str:
    """Turn arbitrary text into a provider-safe identifier.

    Collapses separator runs and guarantees an alphabetic/underscore lead, the
    same contract ``core.mcp_manager`` applies to MCP tool names.
    """
    cleaned = _VALID_NAME_CHARS.sub("_", str(value or ""))
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    cleaned = cleaned.strip("_")
    if not cleaned:
        cleaned = "skill"
    if not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = f"skill_{cleaned}"
    return cleaned


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "y")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


# --------------------------------------------------------------------------- #
# Definition parsing (yaml / json / markdown frontmatter)
# --------------------------------------------------------------------------- #


def _parse_yaml(text: str) -> Optional[Dict[str, Any]]:
    """Parse YAML with PyYAML. Optional dependency: a missing runtime is not fatal."""
    try:
        import yaml  # type: ignore
    except ImportError:
        logger.warning("[SkillsLibrary] PyYAML not installed; .yaml skills will be skipped. Use .json.")
        return None
    try:
        data = yaml.safe_load(text)
    except Exception as exc:
        logger.warning("[SkillsLibrary] Unreadable YAML skill: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(text)
    except Exception as exc:
        logger.warning("[SkillsLibrary] Unreadable JSON skill: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def _parse_markdown(text: str) -> Optional[Dict[str, Any]]:
    """Parse a Markdown skill with optional ``---`` YAML frontmatter.

    With no frontmatter the whole body becomes ``instructions`` — the simplest
    possible "skill" is just prose.
    """
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith("---"):
        return {"instructions": stripped.strip()}
    lines = stripped.splitlines()
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end is None:
        return {"instructions": stripped.strip()}
    front = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :]).strip()
    data = _parse_yaml(front) or {}
    if not isinstance(data, dict):
        data = {}
    if body and not str(data.get("instructions") or "").strip():
        data["instructions"] = body
    return data


def _read_definition(path: Path) -> Optional[Tuple[Dict[str, Any], str]]:
    """Read one skill definition, returning ``(mapping, checksum)``.

    The file is read exactly once: the checksum is taken from the same bytes that
    were parsed, so a 10,000-skill scan never pays for a second read per skill.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("[SkillsLibrary] Cannot read %s: %s", path, exc)
        return None
    checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()

    name = path.name.lower()
    suffix = path.suffix.lower()
    if name == "skill.md":
        data = _parse_markdown(text)
    elif suffix in (".yaml", ".yml"):
        data = _parse_yaml(text)
    elif suffix == ".json":
        data = _parse_json(text)
    elif suffix == ".md":
        data = _parse_markdown(text)
    else:
        data = None
    if not data:
        return None
    return data, checksum


def _spec_from_mapping(
    data: Dict[str, Any],
    fallback_id: str,
    source: str,
    path: Path,
    checksum: str = "",
) -> Optional[SkillSpec]:
    """Build a :class:`SkillSpec` from a parsed mapping, dropping unknown keys."""
    if not isinstance(data, dict):
        return None
    sid = str(data.get("id") or data.get("skill_id") or fallback_id or "").strip()
    if not sid:
        return None

    parameters = data.get("parameters")
    if not isinstance(parameters, dict) or not parameters:
        parameters = {"type": "OBJECT", "properties": {}}

    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    return SkillSpec(
        skill_id=sid,
        name=str(data.get("name") or sid).strip(),
        description=str(data.get("description") or "").strip(),
        version=str(data.get("version") or "1.0.0"),
        category=str(data.get("category") or "general").strip(),
        tags=_as_str_list(data.get("tags")),
        active=_coerce_bool(data.get("active", True), True),
        executable=_coerce_bool(data.get("executable", False), False),
        tool_name=str(data.get("tool_name") or "").strip(),
        parameters=parameters,
        instructions=str(data.get("instructions") or "").strip(),
        manual=str(data.get("manual") or "").strip(),
        dependencies=_as_str_list(data.get("dependencies")),
        metadata=metadata,
        source=source,
        path=path,
        checksum=checksum,
    )


def _meta_from_spec(spec: SkillSpec) -> SkillMeta:
    mtime = 0.0
    size = 0
    if spec.path is not None:
        try:
            stat = spec.path.stat()
            mtime, size = stat.st_mtime, stat.st_size
        except OSError:
            pass
    return SkillMeta(
        skill_id=spec.skill_id,
        name=spec.name,
        description=spec.description,
        category=spec.category,
        tags=list(spec.tags),
        executable=spec.executable,
        active=spec.active,
        source=spec.source,
        tool_name=spec.tool_name,
        path=spec.path,
        version=spec.version,
        mtime=mtime,
        size=size,
        checksum=spec.checksum,
    )


# --------------------------------------------------------------------------- #
# The library
# --------------------------------------------------------------------------- #


class SkillsLibrary:
    """A lazy, file-backed catalogue of skills.

    Construction is cheap: nothing is read until :meth:`refresh` (or any
    accessor, which calls :meth:`ensure_loaded` first). ``refresh`` reads every
    definition **once** to build the lightweight catalogue but deliberately does
    not retain bodies.
    """

    def __init__(
        self,
        builtin_dir: Optional[Path] = None,
        user_dir: Optional[Path] = None,
        *,
        cache_size: int = DEFAULT_CACHE_SIZE,
        enabled: bool = True,
    ) -> None:
        self.builtin_dir = Path(builtin_dir) if builtin_dir is not None else BUILTIN_SKILLS_DIR
        self.user_dir = Path(user_dir) if user_dir is not None else (get_user_data_dir() / "skills_library")
        self._cache_size = max(8, int(cache_size))
        self._enabled = bool(enabled)

        self._lock = threading.RLock()
        self._meta: "OrderedDict[str, SkillMeta]" = OrderedDict()
        self._rows: List[SkillMeta] = []
        self._inverted: Dict[str, List[int]] = {}
        self._cache: "OrderedDict[str, SkillSpec]" = OrderedDict()
        self._executor_cache: Dict[str, Any] = {}
        self._disabled: set = set()
        self._published: Dict[str, str] = {}  # tool_name -> skill_id
        self._loaded = False
        self._stats = LibraryStats(enabled=enabled, cached=0)

    # -- roots -------------------------------------------------------------- #

    def roots(self) -> List[Tuple[str, Path]]:
        """``[(source, path), ...]`` in override order (later wins)."""
        return [("builtin", self.builtin_dir), ("user", self.user_dir)]

    # -- discovery ---------------------------------------------------------- #

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.refresh()

    def _iter_sources(self, root: Path) -> Iterable[Tuple[Path, str]]:
        """Yield ``(definition_path, fallback_id)`` for every skill under ``root``."""
        if not root.exists():
            return
        try:
            items = sorted(root.iterdir(), key=lambda p: p.name.lower())
        except OSError as exc:
            logger.warning("[SkillsLibrary] Cannot scan %s: %s", root, exc)
            return

        for item in items:
            if item.name.startswith((".", "_")):
                continue
            if item.is_dir():
                for filename in SKILL_FILENAMES:
                    candidate = item / filename
                    if candidate.is_file():
                        yield candidate, item.name
                        break
            elif item.is_file() and item.suffix.lower() in SINGLE_FILE_SUFFIXES:
                if item.name.lower() in SKILL_FILENAMES or item.stem.lower() in _IGNORED_STEMS:
                    continue
                yield item, item.stem

    def refresh(self, force: bool = False) -> LibraryStats:
        """(Re)build the lightweight catalogue. Idempotent unless ``force``."""
        with self._lock:
            if self._loaded and not force:
                return self._stats
            started = time.perf_counter()

            meta: "OrderedDict[str, SkillMeta]" = OrderedDict()
            for source, root in self.roots():
                for path, fallback_id in self._iter_sources(root):
                    read = _read_definition(path)
                    if read is None:
                        continue
                    data, checksum = read
                    spec = _spec_from_mapping(data, fallback_id, source, path, checksum)
                    if spec is None:
                        continue
                    if spec.executable and not self._has_executor(spec):
                        logger.warning(
                            "[SkillsLibrary] Skill '%s' is marked executable but has no executor.py; "
                            "treating it as instructional.",
                            spec.skill_id,
                        )
                        spec.executable = False
                    if spec.skill_id in meta:
                        meta.move_to_end(spec.skill_id)
                    meta[spec.skill_id] = _meta_from_spec(spec)

            self._meta = meta
            self._rows = list(meta.values())
            self._rebuild_inverted()
            self._cache.clear()
            self._executor_cache.clear()
            self._loaded = True

            for skill_id in list(self._disabled):
                if skill_id not in meta:
                    self._disabled.discard(skill_id)

            self._stats = LibraryStats(
                skills=len(self._rows),
                executable=sum(1 for m in self._rows if m.executable),
                active=sum(1 for m in self._rows if m.active and m.skill_id not in self._disabled),
                disabled=len(self._disabled),
                builtin=sum(1 for m in self._rows if m.source == "builtin"),
                user=sum(1 for m in self._rows if m.source == "user"),
                cached=0,
                last_scan_ms=round((time.perf_counter() - started) * 1000.0, 2),
                enabled=self._enabled,
            )
            logger.info(
                "[SkillsLibrary] catalogue: %d skill(s) (%d executable) from %s in %.0f ms",
                self._stats.skills,
                self._stats.executable,
                ", ".join(str(r) for _, r in self.roots()),
                self._stats.last_scan_ms,
            )
            return self._stats

    def _rebuild_inverted(self) -> None:
        inverted: Dict[str, List[int]] = {}
        for row, meta in enumerate(self._rows):
            toks = (
                meta.name_tokens()
                | meta.tag_tokens()
                | set(_tokens(meta.description))
                | set(_tokens(meta.category))
            )
            for tok in toks:
                inverted.setdefault(tok, []).append(row)
        self._inverted = inverted

    def _has_executor(self, spec: SkillSpec) -> bool:
        directory = spec.directory
        return bool(directory and (directory / "executor.py").is_file())

    # -- catalogue access --------------------------------------------------- #

    def entries(self, include_disabled: bool = True) -> List[SkillMeta]:
        self.ensure_loaded()
        with self._lock:
            if include_disabled:
                return list(self._rows)
            return [m for m in self._rows if self._is_available(m)]

    def _is_available(self, meta: SkillMeta) -> bool:
        if not self._enabled:
            return False
        return meta.active and meta.skill_id not in self._disabled

    def has(self, skill_id: str) -> bool:
        self.ensure_loaded()
        with self._lock:
            return skill_id in self._meta

    def meta(self, skill_id: str) -> Optional[SkillMeta]:
        self.ensure_loaded()
        with self._lock:
            return self._meta.get(skill_id)

    # -- full materialisation (lazy) ---------------------------------------- #

    def get(self, skill_id: str, *, include_disabled: bool = True) -> Optional[SkillSpec]:
        """Materialise one skill, or ``None`` when unknown/unavailable."""
        self.ensure_loaded()
        with self._lock:
            meta = self._meta.get(skill_id)
            if meta is None:
                return None
            if not include_disabled and not self._is_available(meta):
                return None
            cached = self._cache.get(skill_id)
            if cached is not None and cached.checksum and cached.checksum == meta.checksum:
                self._cache.move_to_end(skill_id)
                return cached

            if meta.path is None:
                return None
            read = _read_definition(meta.path)
            if read is None:
                return None
            data, checksum = read
            spec = _spec_from_mapping(data, meta.skill_id, meta.source, meta.path, checksum)
            if spec is None:
                return None
            if spec.executable and not self._has_executor(spec):
                spec.executable = False
            self._remember(spec)
            return spec

    def _remember(self, spec: SkillSpec) -> None:
        self._cache[spec.skill_id] = spec
        self._cache.move_to_end(spec.skill_id)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        self._stats.cached = len(self._cache)

    # -- search ------------------------------------------------------------- #

    def search(
        self,
        query: str,
        limit: int = 6,
        *,
        executable_only: bool = False,
        include_disabled: bool = False,
    ) -> List[SkillHit]:
        """Rank the catalogue against a natural-language intent.

        A weighted token-overlap scorer over a prebuilt inverted index — cheaper
        and smaller than a dense-vector index, which matters when the catalogue
        is expected to reach tens of thousands of rows.
        """
        started = time.perf_counter()
        self.ensure_loaded()
        query = (query or "").strip()
        if not query:
            return []
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 6

        q_tokens = _tokens(query)
        if not q_tokens:
            return []
        q_set = set(q_tokens)
        flat_query = re.sub(r"[^a-z0-9]+", " ", query.lower()).strip()

        with self._lock:
            candidates: set = set()
            for tok in q_tokens:
                candidates.update(self._inverted.get(tok, ()))
            # Substring tolerance: the query word may be a prefix of an indexed
            # token ("review" -> "reviewer"). Only worth the scan when the direct
            # hits are thin, so a large catalogue stays fast.
            if len(candidates) < limit * 3:
                for tok in q_tokens:
                    if len(tok) < 4:
                        continue
                    for key, rows in self._inverted.items():
                        if key != tok and key.startswith(tok):
                            candidates.update(rows)

            hits: List[SkillHit] = []
            for row in candidates:
                meta = self._rows[row]
                if executable_only and not meta.executable:
                    continue
                if not include_disabled and not self._is_available(meta):
                    continue

                name_toks = meta.name_tokens()
                tag_toks = meta.tag_tokens()
                desc_toks = set(_tokens(meta.description))
                cat_toks = set(_tokens(meta.category))

                score = (
                    3.0 * len(q_set & name_toks)
                    + 2.5 * len(q_set & tag_toks)
                    + 2.0 * len(q_set & cat_toks)
                    + 1.0 * len(q_set & desc_toks)
                )
                flat_id = re.sub(r"[^a-z0-9]+", " ", meta.skill_id.lower()).strip()
                flat_name = re.sub(r"[^a-z0-9]+", " ", meta.name.lower()).strip()
                if flat_query and flat_query == flat_id:
                    score += 12.0
                elif flat_id and flat_id in flat_query:
                    score += 5.0
                if flat_name and flat_name in flat_query:
                    score += 4.0
                if meta.executable:
                    score += 0.5  # small tie-break toward actionable skills
                if score > 0.0:
                    hits.append(SkillHit(meta=meta, score=score))

            hits.sort(key=lambda h: (-h.score, h.skill_id))
            result = hits[:limit]

        self._stats.last_query_ms = round((time.perf_counter() - started) * 1000.0, 2)
        return result

    # -- enable / disable --------------------------------------------------- #

    def set_disabled(self, skill_ids: Iterable[str]) -> None:
        with self._lock:
            self._disabled = {str(s) for s in (skill_ids or []) if str(s)}
            if self._loaded:
                self._stats.disabled = len(self._disabled)
                self._stats.active = sum(1 for m in self._rows if self._is_available(m))

    def disable(self, skill_id: str) -> None:
        with self._lock:
            if skill_id:
                self._disabled.add(skill_id)
                self._stats.disabled = len(self._disabled)

    def enable(self, skill_id: str) -> None:
        with self._lock:
            self._disabled.discard(skill_id)
            self._stats.disabled = len(self._disabled)

    def disabled(self) -> set:
        with self._lock:
            return set(self._disabled)

    # -- execution of executable skills ------------------------------------- #

    def _executor_module(self, spec: SkillSpec) -> Any:
        directory = spec.directory
        if directory is None:
            raise FileNotFoundError(f"Skill '{spec.skill_id}' has no directory.")
        code_path = directory / "executor.py"
        if not code_path.is_file():
            raise FileNotFoundError(f"Skill '{spec.skill_id}' has no executor.py.")
        key = str(code_path.resolve())
        with self._lock:
            cached = self._executor_cache.get(key)
        if cached is not None:
            return cached

        module_name = f"brahma_skill_lib_{sanitize_identifier(spec.skill_id)}_{abs(hash(key))}"
        import_spec = importlib.util.spec_from_file_location(module_name, str(code_path))
        if not import_spec or not import_spec.loader:
            raise ImportError(f"Unable to load executor for skill '{spec.skill_id}'.")
        module = importlib.util.module_from_spec(import_spec)
        sys.modules[import_spec.name] = module
        import_spec.loader.exec_module(module)
        with self._lock:
            self._executor_cache[key] = module
        return module

    def execute_skill(self, skill_id: str, **kwargs: Any) -> Any:
        """Run an executable skill. Safe to call from a worker thread."""
        spec = self.get(skill_id, include_disabled=False)
        if spec is None:
            raise KeyError(f"No available skill named '{skill_id}'.")
        if not spec.executable:
            raise TypeError(f"Skill '{skill_id}' is not executable.")
        module = self._executor_module(spec)
        func = getattr(module, "execute", None)
        if not callable(func):
            raise AttributeError(f"Skill '{skill_id}' has no callable 'execute'.")
        if inspect.iscoroutinefunction(func):
            import asyncio

            return asyncio.run(func(**kwargs))
        return func(**kwargs)

    def executor_for(self, skill_id: str) -> Callable[..., Any]:
        """A synchronous callable bound to one skill (fresh spec on every call)."""

        def _exec(**kwargs: Any) -> Any:
            return self.execute_skill(skill_id, **kwargs)

        return _exec

    # -- publishing into the execution surface ------------------------------ #

    def publish(self, registry: Any = None) -> int:
        """Register every *available executable* skill as an external tool.

        Instructional skills are never published — they are pure context and are
        served only through ``load_skill``. Idempotent: a repeated call refreshes
        the same names, and any previously-published skill that disappeared or
        was disabled is unregistered again.
        """
        registry = registry or self._registry()
        if registry is None:
            logger.warning("[SkillsLibrary] No execution registry available; skipping publish.")
            return 0
        if not hasattr(registry, "register_external_tool"):
            # A read-only or minimal registry is a legitimate case (tests,
            # alternate hosts). Nothing to publish, and nothing to warn about.
            logger.info("[SkillsLibrary] Registry does not accept external tools; skipping publish.")
            return 0

        published = 0
        wanted: Dict[str, str] = {}
        for meta in self.entries(include_disabled=True):
            if not meta.executable or not self._is_available(meta):
                continue
            spec = self.get(meta.skill_id)
            if spec is None or not spec.executable:
                continue
            tool_name = spec.resolved_tool_name()
            wanted[tool_name] = spec.skill_id
            try:
                registry.register_external_tool(
                    tool_name,
                    description=spec.description or f"Skill {spec.name or spec.skill_id}",
                    parameters=spec.parameters,
                    executor=self.executor_for(spec.skill_id),
                    source=SKILL_SOURCE,
                )
                published += 1
            except Exception as exc:
                logger.warning("[SkillsLibrary] Cannot publish skill '%s': %s", spec.skill_id, exc)

        for tool_name in list(self._published):
            if tool_name not in wanted:
                try:
                    registry.unregister_external_tool(tool_name)
                except Exception:
                    pass
        self._published = wanted
        logger.info("[SkillsLibrary] published %d executable skill(s).", published)
        return published

    @staticmethod
    def _registry() -> Any:
        try:
            from core.dynamic_registry import DynamicToolRegistry

            return DynamicToolRegistry
        except Exception:
            return None

    # -- diagnostics -------------------------------------------------------- #

    def manifest(self) -> Dict[str, Any]:
        """Read-only status for the Settings UI / logs."""
        stats = self.stats()
        return {
            "enabled": stats.enabled,
            "skills": stats.skills,
            "executable": stats.executable,
            "active": stats.active,
            "disabled": stats.disabled,
            "builtin": stats.builtin,
            "user": stats.user,
            "cached": stats.cached,
            "last_scan_ms": stats.last_scan_ms,
            "last_query_ms": stats.last_query_ms,
            "roots": [{"source": s, "path": str(p)} for s, p in self.roots()],
        }

    def stats(self) -> LibraryStats:
        if not self._loaded:
            return self.refresh()
        with self._lock:
            self._stats.cached = len(self._cache)
            return self._stats

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)
            self._stats.enabled = self._enabled

    @property
    def enabled(self) -> bool:
        return self._enabled


# --------------------------------------------------------------------------- #
# Process-wide instance
# --------------------------------------------------------------------------- #

_library_lock = threading.Lock()
_library: Optional[SkillsLibrary] = None


def get_library() -> SkillsLibrary:
    """Returns the process-wide library, creating it on first use."""
    global _library
    with _library_lock:
        if _library is None:
            _library = SkillsLibrary()
        return _library


def configure_library(
    *,
    builtin_dir: Optional[Path] = None,
    user_dir: Optional[Path] = None,
    cache_size: Optional[int] = None,
    enabled: Optional[bool] = None,
    disabled: Optional[Iterable[str]] = None,
) -> SkillsLibrary:
    """Creates or re-points the process-wide library, then applies settings.

    Used at startup (to honour an install that stores skills elsewhere) and by
    the test suite to isolate an instance.
    """
    global _library
    with _library_lock:
        lib = _library
        if lib is None:
            lib = SkillsLibrary(builtin_dir, user_dir, cache_size=cache_size or DEFAULT_CACHE_SIZE)
            _library = lib
        else:
            if builtin_dir is not None:
                lib.builtin_dir = Path(builtin_dir)
                lib._loaded = False  # noqa: SLF001 - deliberate catalogue invalidation
            if user_dir is not None:
                lib.user_dir = Path(user_dir)
                lib._loaded = False  # noqa: SLF001 - deliberate catalogue invalidation
    if enabled is not None:
        lib.set_enabled(enabled)
    if disabled is not None:
        lib.set_disabled(disabled)
    return lib


def reset_library() -> None:
    """Drops the process-wide instance. Test/DI seam."""
    global _library
    with _library_lock:
        _library = None


__all__ = [
    "BUILTIN_SKILLS_DIR",
    "DEFAULT_CACHE_SIZE",
    "LibraryStats",
    "SKILL_FILENAMES",
    "SKILL_SOURCE",
    "SkillHit",
    "SkillMeta",
    "SkillSpec",
    "SkillsLibrary",
    "configure_library",
    "get_library",
    "reset_library",
    "sanitize_identifier",
]
