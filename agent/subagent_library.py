"""A searchable library of specialist subagent templates.

The harness could already delegate, but delegation had one shape: a generic
child running :data:`agent.subagent.SUBAGENT_SYSTEM` with a filtered tool
surface. A corpus of specialist agent definitions (600+ markdown files, ~10 MB)
is far too large to put in a prompt, and globbing it would cost turns.

Progressive disclosure, the same shape as project skills:

* the resident prompt carries a compact category index — counts plus a few
  example slugs, never the 600 bodies;
* ``list_subagents`` answers a keyword or category query with slugs and
  one-line descriptions;
* ``run_subagent(subagent_type=<slug>)`` loads exactly one template and uses
  its body as the child's brief.

The corpus is vendored in this repository under ``subagents/`` (the catalogue
plus every body it names), so a checkout is self-contained: the Obsidian vault
it was ported from does not travel with the code. Resolution order is
``BRAHMA_SUBAGENT_LIBRARY``, a ``config/subagents.json`` file (user data, then
the repo), the bundled ``subagents/`` folder, and finally the old Obsidian
location for a machine that still has one. When none is present every entry
point degrades to "not installed" instead of failing: CI imports this module
with only pytest installed, and a missing optional corpus must not break the
harness.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .spec import ToolSpec, register

#: Environment override: the library root (or a direct path to ``catalog.json``).
#: Set but empty disables discovery, mirroring ``BRAHMA_SKILLS_DIR``.
LIBRARY_ENV = "BRAHMA_SUBAGENT_LIBRARY"

#: The machine-readable index the library ships. Rows carry name, slug, kind,
#: category, description, tools, bytes, sha256 and a root-relative path.
CATALOG_FILE = "catalog.json"

#: Optional config file: ``{"library": "<root>"}``.
CONFIG_FILE = "subagents.json"

#: The model-facing catalogue tool. Never present on a child's surface.
LIBRARY_TOOL = "list_subagents"

#: A catalogue this large is not an index; refuse it rather than hold it.
MAX_CATALOG_BYTES = 8 * 1024 * 1024

#: One template body becomes a child's system prompt, so it is bounded. The
#: library median is ~16 KB and the 95th percentile ~40 KB; the cap keeps an
#: outlier from crowding out the child's instructions and tool list.
MAX_TEMPLATE_CHARS = 24_000

#: Resident category index budget. Small on purpose: this block is paid for on
#: every turn of every text session, and the model can always ask for detail.
MAX_PROMPT_CHARS = 1_600

#: How many example slugs one category shows in the resident index.
MAX_PROMPT_EXAMPLES = 3

MAX_DESCRIPTION = 220
DEFAULT_RESULTS = 12
MAX_RESULTS = 50
MAX_OUTPUT_CHARS = 9_000

#: The library shipped with this repository. This is the default source; the
#: vault the corpus was ported from is only a legacy fallback.
BUNDLED_LIBRARY = "subagents"

#: Legacy discovery, relative to the user's home directory. Kept so a machine
#: that still has the original Obsidian copy keeps working, but it is never
#: required: the bundled tree wins whenever it is present.
DEFAULT_LIBRARY = ("Documents", "Obsidian", "My Vault", "subagent-orchestration")

_CACHE: Dict[str, Tuple[float, int, "SubagentCatalog"]] = {}


# --------------------------------------------------------------------------- #
# Location
# --------------------------------------------------------------------------- #

def _resolved(path: str | Path) -> Path:
    try:
        return Path(path).expanduser().resolve()
    except OSError:
        return Path(path).expanduser()


def _as_library_root(path: str | Path) -> Optional[Path]:
    """Return ``path`` as a directory holding a catalog, or ``None``."""
    candidate = _resolved(path)
    try:
        if candidate.is_file():
            return candidate.parent if candidate.name.lower() == CATALOG_FILE else None
        if candidate.is_dir() and (candidate / CATALOG_FILE).is_file():
            return candidate
    except OSError:
        return None
    return None


def _config_roots() -> List[Path]:
    """``config/subagents.json`` in user data, then in the repo."""
    paths: List[Path] = []
    try:
        from core.user_paths import get_user_data_dir

        paths.append(Path(get_user_data_dir()) / "config" / CONFIG_FILE)
    except Exception:
        pass
    paths.append(Path(__file__).resolve().parent.parent / "config" / CONFIG_FILE)
    return paths


def _root_from_config(path: Path) -> Optional[Path]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, Mapping):
        return None
    for key in ("library", "library_path", "root", "path"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return _as_library_root(value.strip())
    return None


def library_root() -> Optional[Path]:
    """The first library that exists, or ``None``.

    An explicit ``BRAHMA_SUBAGENT_LIBRARY`` is the whole answer: a test or a
    deployment that pins one tree must not silently fall back to the user's
    vault. An empty value disables the library entirely.
    """
    if LIBRARY_ENV in os.environ:
        raw = os.getenv(LIBRARY_ENV, "").strip()
        return _as_library_root(raw) if raw else None
    for config in _config_roots():
        try:
            if config.is_file():
                root = _root_from_config(config)
                if root is not None:
                    return root
        except OSError:
            continue
    # The bundled tree is the deployment default; the vault is only a fallback
    # for a checkout that predates the port (or had its `subagents/` removed).
    candidates = [
        Path(__file__).resolve().parent.parent / BUNDLED_LIBRARY,
        Path.home().joinpath(*DEFAULT_LIBRARY),
    ]
    for candidate in candidates:
        root = _as_library_root(candidate)
        if root is not None:
            return root
    return None


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SubagentTemplate:
    """One row of the library index. The body is read only when dispatched."""

    slug: str
    name: str
    category: str
    description: str
    path: str
    kind: str = "agent"
    tools: Tuple[str, ...] = ()
    bytes: int = 0

    @property
    def is_agent(self) -> bool:
        return self.kind == "agent"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "category": self.category,
            "kind": self.kind,
            "description": self.description,
            "path": self.path,
            "tools": list(self.tools),
            "bytes": self.bytes,
        }


@dataclass(frozen=True)
class SubagentCatalog:
    """The loaded index: rows plus the root their paths are relative to."""

    root: Path
    source: Path
    entries: Tuple[SubagentTemplate, ...] = ()
    diagnostics: Tuple[str, ...] = field(default_factory=tuple)

    # -- queries -----------------------------------------------------------

    @property
    def agents(self) -> Tuple[SubagentTemplate, ...]:
        return tuple(entry for entry in self.entries if entry.is_agent)

    def categories(self) -> List[Tuple[str, int]]:
        """``(category, count)``, most populated first, then alphabetical."""
        counts: Dict[str, int] = {}
        for entry in self.agents:
            counts[entry.category] = counts.get(entry.category, 0) + 1
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    def search(
        self,
        query: str = "",
        *,
        category: str = "",
        limit: Optional[int] = None,
    ) -> List[SubagentTemplate]:
        """Rank templates by keyword and/or category.

        Every query token must appear in the slug, name or description; the
        ranking prefers an exact slug, then a prefix, then slug/name/description
        hits. Agents outrank the few ``kind: doc`` rows on a tie.
        """
        text = (query or "").strip().lower()
        wanted_category = _norm(category)
        tokens = _tokens(text)
        matches: List[Tuple[int, SubagentTemplate]] = []
        for entry in self.entries:
            if wanted_category and not _category_matches(entry.category, wanted_category):
                continue
            if entry.kind != "agent" and wanted_category:
                continue
            score = _score(entry, text, tokens)
            if score <= 0:
                continue
            matches.append((score, entry))
        matches.sort(
            key=lambda item: (-item[0], len(item[1].slug), item[1].slug.lower())
        )
        rows = [entry for _score_value, entry in matches]
        if limit is not None:
            return rows[: max(0, int(limit))]
        return rows

    def get(self, name: str) -> Optional[SubagentTemplate]:
        """Resolve a slug, exact name, unique prefix, or unique substring."""
        raw = (name or "").strip()
        if not raw:
            return None
        lowered = raw.lower()
        normalised = _norm(raw)
        for entry in self.entries:
            if entry.slug.lower() == lowered or entry.name.lower() == lowered:
                return entry
        for entry in self.entries:
            if _norm(entry.slug) == normalised or _norm(entry.name) == normalised:
                return entry
        for predicate in (
            lambda entry: _norm(entry.slug).startswith(normalised),
            lambda entry: normalised in _norm(entry.slug),
        ):
            hits = [entry for entry in self.entries if predicate(entry)]
            if len(hits) == 1:
                return hits[0]
        return None

    def suggest(self, name: str, limit: int = 5) -> List[SubagentTemplate]:
        """Closest rows for a name that did not resolve, as a retry hint."""
        rows = self.search(name, limit=limit)
        if rows:
            return rows
        tokens = _tokens(name)
        if tokens:
            rows = self.search(tokens[0], limit=limit)
        return rows

    # -- bodies ------------------------------------------------------------

    def template_path(self, entry: SubagentTemplate) -> Optional[Path]:
        """The template file for ``entry``, refusing paths outside the root."""
        raw = Path(entry.path)
        candidate = raw if raw.is_absolute() else self.root / raw
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if not raw.is_absolute():
            try:
                resolved.relative_to(self.root.resolve())
            except ValueError:
                return None
        try:
            return resolved if resolved.is_file() else None
        except OSError:
            return None

    def template_body(
        self,
        entry: SubagentTemplate,
        *,
        max_chars: int = MAX_TEMPLATE_CHARS,
    ) -> str:
        """Read one template and return the instructions without frontmatter.

        Raises ``OSError`` when the file is missing; callers turn that into a
        tool error because a catalogue row without its body is a broken library,
        not a reason to run an untemplated child.
        """
        path = self.template_path(entry)
        if path is None:
            raise OSError(f"template file for {entry.slug!r} is missing or outside {self.root}")
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        body = _strip_frontmatter(text).strip()
        if len(body) > max_chars:
            body = (
                body[:max_chars].rstrip()
                + f"\n\n[Template truncated at {max_chars} characters.]"
            )
        return body

    # -- resident prompt ---------------------------------------------------

    def prompt_block(
        self,
        *,
        max_chars: int = MAX_PROMPT_CHARS,
        dispatch_available: bool = True,
    ) -> str:
        """The compact category index placed in the system prompt.

        Counts and example slugs only. The names are the model's hint about
        *what to ask for*, and ``list_subagents`` is how it gets the full
        picture without paying for 600 descriptions on every turn.

        ``dispatch_available=False`` is for a surface that can look the library
        up but has no text completer to build a child loop (Gemini Live). It
        must not promise ``run_subagent`` there: a tool the model cannot call
        is worse than no hint at all.
        """
        agents = self.agents
        if not agents:
            return ""

        if dispatch_available:
            guidance = (
                f"Call `{LIBRARY_TOOL}` with a keyword or a category to get "
                "slugs and one-line descriptions, then delegate with "
                "`run_subagent(subagent_type=\"<slug>\", task=\"...\")`; the "
                "child then runs with that template as its brief. Prefer a "
                "specialist over a generic child when the work matches."
            )
        else:
            guidance = (
                f"Call `{LIBRARY_TOOL}` with a keyword or a category to get "
                "slugs and one-line descriptions. This surface cannot dispatch "
                "a specialist: `run_subagent` exists only in the text/CLI "
                "harness. Use the lookup to answer what is available and never "
                "claim you can run one here."
            )

        lines = [
            "## Subagent library",
            "",
            f"{len(agents)} specialist subagent templates are installed. The "
            "resident prompt cannot list them all.",
            guidance,
            "",
            "Categories: "
            + ", ".join(f"{name} ({count})" for name, count in self.categories())
            + ".",
        ]

        by_category: Dict[str, List[str]] = {}
        for entry in sorted(agents, key=lambda item: item.slug.lower()):
            bucket = by_category.setdefault(entry.category, [])
            if len(bucket) < MAX_PROMPT_EXAMPLES:
                bucket.append(entry.slug)
        examples = "; ".join(
            f"{name}: " + ", ".join(f"`{slug}`" for slug in slugs)
            for name, slugs in sorted(by_category.items())
            if slugs
        )
        if examples:
            lines.append(f"Examples: {examples}.")

        text = "\n".join(lines)
        if len(text) > max_chars:
            # The examples are the first thing a tight budget can lose; the
            # categories and the tool name are the part that must survive.
            text = text[: max_chars - 40].rstrip() + "\n- see the tool for detail."
        return text


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _tokens(text: str) -> List[str]:
    return [part for part in re.split(r"[^a-z0-9]+", (text or "").lower()) if part]


def _norm(text: str) -> str:
    return "-".join(_tokens(text))


def _category_matches(category: str, wanted: str) -> bool:
    actual = _norm(category) or "uncategorized"
    return actual == wanted or actual.startswith(wanted) or wanted in actual


def _score(entry: SubagentTemplate, query: str, tokens: Sequence[str]) -> int:
    if not tokens:
        return 2 if entry.is_agent else 1
    slug = _norm(entry.slug)
    name = _norm(entry.name)
    slug_tokens = _tokens(entry.slug)
    description = entry.description.lower()
    score = 0
    for token in tokens:
        if token in slug_tokens:
            score += 30
        elif any(part.startswith(token) for part in slug_tokens):
            # "review" should find "code-reviewer"; a plain substring test
            # misses the inflection and a stemmer is a dependency this module
            # cannot take.
            score += 24
        elif token in slug:
            score += 18
        elif token in name:
            score += 14
        elif token in description:
            score += 6
        else:
            return 0
    query_norm = _norm(query)
    if query_norm and (slug == query_norm or name == query_norm):
        score += 100
    elif query_norm and (slug.startswith(query_norm) or name.startswith(query_norm)):
        score += 40
    if all(any(part.startswith(token) for part in slug_tokens) for token in tokens):
        # Between two slugs that cover the query, the tighter one is the better
        # match: "code-reviewer" should beat
        # "code-review-feedback-implementation-agent".
        score -= min(8, 3 * max(0, len(slug_tokens) - len(tokens)))
    if entry.is_agent:
        score += 2
    return max(score, 1)


def _strip_frontmatter(text: str) -> str:
    """Drop a closed ``---`` header; text without one is all body."""
    if not text.startswith("---"):
        return text
    lines = text.splitlines(keepends=True)
    if lines[0].strip() != "---":
        return text
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            return "".join(lines[index + 1 :])
    return text


def _as_string_list(value: Any) -> Tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _parse_entry(row: Any) -> Optional[SubagentTemplate]:
    if not isinstance(row, Mapping):
        return None
    slug = str(row.get("slug") or row.get("name") or "").strip()
    if not slug:
        return None
    name = str(row.get("name") or slug).strip()
    category = _norm(str(row.get("category") or "")) or "uncategorized"
    description = " ".join(str(row.get("description") or "").split())
    if len(description) > MAX_DESCRIPTION:
        description = description[: MAX_DESCRIPTION - 1].rstrip() + "…"
    try:
        size = int(row.get("bytes") or 0)
    except (TypeError, ValueError):
        size = 0
    return SubagentTemplate(
        slug=slug,
        name=name,
        category=category,
        description=description or "No description provided.",
        path=str(row.get("path") or "").strip(),
        kind=str(row.get("kind") or "agent").strip().lower() or "agent",
        tools=_as_string_list(row.get("tools")),
        bytes=max(0, size),
    )


def _parse_catalog(source: Path, data: Any) -> SubagentCatalog:
    rows = data.get("agents") if isinstance(data, Mapping) else data
    if not isinstance(rows, (list, tuple)):
        raise ValueError("catalog must be a list of rows (or an object with 'agents')")
    entries: List[SubagentTemplate] = []
    diagnostics: List[str] = []
    seen: Dict[str, SubagentTemplate] = {}
    for index, row in enumerate(rows):
        entry = _parse_entry(row)
        if entry is None:
            diagnostics.append(f"row {index}: ignored, no slug or name")
            continue
        key = entry.slug.lower()
        if key in seen:
            diagnostics.append(
                f"row {index}: duplicate slug {entry.slug!r}; first row wins"
            )
            continue
        seen[key] = entry
        entries.append(entry)
    return SubagentCatalog(
        root=source.parent,
        source=source,
        entries=tuple(entries),
        diagnostics=tuple(diagnostics),
    )


def load(root: Optional[str | Path] = None) -> Optional[SubagentCatalog]:
    """Load (and cache) the catalogue, or return ``None`` when not installed.

    The cache is keyed by the catalogue path and invalidated by mtime+size, so
    editing the library needs no restart and a malformed edit keeps the last
    good index instead of emptying the catalogue mid-session.
    """
    library = _as_library_root(root) if root is not None else library_root()
    if library is None:
        return None
    source = library / CATALOG_FILE
    try:
        stat = source.stat()
    except OSError:
        return None
    if stat.st_size > MAX_CATALOG_BYTES:
        return None
    key = os.path.normcase(str(source))
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]
    try:
        data = json.loads(source.read_text(encoding="utf-8-sig"))
        catalog = _parse_catalog(source, data)
    except (OSError, ValueError):
        return cached[2] if cached is not None else None
    if not catalog.entries:
        return cached[2] if cached is not None else catalog
    _CACHE[key] = (stat.st_mtime, stat.st_size, catalog)
    return catalog


def clear_cache() -> None:
    """Drop the loaded catalogues (tests and library swaps)."""
    _CACHE.clear()


def prompt_block(*, dispatch_available: bool = True) -> str:
    """The resident category index, or ``""`` when the library is not installed."""
    catalog = load()
    if catalog is None:
        return ""
    return catalog.prompt_block(dispatch_available=dispatch_available)


# --------------------------------------------------------------------------- #
# Tool
# --------------------------------------------------------------------------- #

BUILTIN_INVENTORY = (
    "Built-in delegation in the text and CLI harness:\n"
    "- `run_subagent`: one bounded child agent with its own context and a "
    "filtered tool surface. Without `subagent_type` it is a generic child; "
    "with one it runs that library template.\n"
    "- `run_subagent_batch`: 1-8 independent read-only children in parallel. "
    "Batch children are always generic.\n"
    "A voice session can look templates up with `list_subagents`, but it has "
    "no child loop and cannot dispatch one."
)


def _clamp_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = DEFAULT_RESULTS
    return max(1, min(limit, MAX_RESULTS))


def _overview(catalog: SubagentCatalog) -> str:
    lines = [
        f"Subagent library: {len(catalog.agents)} templates across "
        f"{len(catalog.categories())} categories.",
        f"Source: {catalog.root}",
        "",
        BUILTIN_INVENTORY,
        "",
        "Categories:",
    ]
    lines.extend(f"- {name} ({count})" for name, count in catalog.categories())
    lines.extend(
        [
            "",
            f"Call `{LIBRARY_TOOL}` with a keyword or one of those categories "
            "to see slugs and one-line descriptions.",
        ]
    )
    return "\n".join(lines)


def _listing(rows: Sequence[SubagentTemplate], total: int, label: str) -> str:
    if not rows:
        return (
            f"No template matched {label}. Try a broader keyword, or call "
            f"`{LIBRARY_TOOL}` with no arguments for the category list."
        )
    lines = [f"{len(rows)} of {total} template(s) for {label}:"]
    used = len(lines[0]) + 1
    for entry in rows:
        line = f"- `{entry.slug}` [{entry.category}] {entry.description}"
        if used + len(line) + 1 > MAX_OUTPUT_CHARS:
            lines.append("- (more results omitted; narrow the query)")
            break
        lines.append(line)
        used += len(line) + 1
    lines.extend(
        [
            "",
            "The text/CLI harness runs one with "
            '`run_subagent(subagent_type="<slug>", task="...")`.',
        ]
    )
    return "\n".join(lines)


def _list_subagents(arguments: Mapping[str, Any], context: Any) -> str:
    args = arguments if isinstance(arguments, Mapping) else {}
    query = str(args.get("query") or "").strip()
    category = str(args.get("category") or "").strip()
    limit = _clamp_limit(args.get("limit"))

    catalog = load()
    if catalog is None:
        return (
            "Subagent template library: not installed.\n\n"
            + BUILTIN_INVENTORY
            + "\n\n"
            f"Set `{LIBRARY_ENV}` (or `config/{CONFIG_FILE}`) to the folder "
            f"that holds `{CATALOG_FILE}` to enable specialist templates."
        )
    if not query and not category:
        return _overview(catalog)

    matches = catalog.search(query, category=category)
    label = " / ".join(
        part
        for part in (
            f"query {query!r}" if query else "",
            f"category {category!r}" if category else "",
        )
        if part
    )
    return _listing(matches[:limit], len(matches), label)


register(
    ToolSpec(
        name=LIBRARY_TOOL,
        description=(
            "Look up the installed specialist subagent templates by keyword or "
            "category. Returns slugs and one-line descriptions; the text/CLI "
            "harness can pass a slug as `subagent_type` to `run_subagent` to "
            "run that specialist. With no arguments it lists the categories "
            "and the built-in delegation tools. Bodies are never loaded by "
            "this call."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Keywords describing the work, e.g. 'code review', "
                        "'seo', 'financial model'. Matched against slug, name "
                        "and description."
                    ),
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Optional category filter, e.g. development, business, "
                        "personal, research, predictions, ai_ml."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max results (default {DEFAULT_RESULTS}, capped at {MAX_RESULTS}).",
                },
            },
        },
        tool_class="read",
        handler=_list_subagents,
        tags=("agent",),
        max_output_chars=MAX_OUTPUT_CHARS,
    )
)


__all__ = [
    "BUNDLED_LIBRARY",
    "BUILTIN_INVENTORY",
    "CATALOG_FILE",
    "CONFIG_FILE",
    "DEFAULT_LIBRARY",
    "DEFAULT_RESULTS",
    "LIBRARY_ENV",
    "LIBRARY_TOOL",
    "MAX_CATALOG_BYTES",
    "MAX_OUTPUT_CHARS",
    "MAX_PROMPT_CHARS",
    "MAX_RESULTS",
    "MAX_TEMPLATE_CHARS",
    "SubagentCatalog",
    "SubagentTemplate",
    "clear_cache",
    "library_root",
    "load",
    "prompt_block",
]
