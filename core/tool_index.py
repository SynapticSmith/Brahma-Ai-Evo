"""
Tool & Skill Index Engine for Brahma AI Evo.

Problem
-------
Brahma Evo advertises every tool it knows about to the model on every Live
session: native `TOOL_DECLARATIONS`, filesystem skills, and every tool exposed
by every connected MCP server. With Calyx (31) + Synapse (82) + native (17)
that is 130 declarations, which is both:

1. **A hard protocol failure.** Gemini Live accepts at most 128 function
   declarations per session and rejects the handshake with
   ``1007 Request contains an invalid argument`` / ``Invalid Argument``.
2. **A token tax.** The declarations are re-sent on the setup frame of every
   session and bloat the effective prompt, degrading routing accuracy
   ("lost in the middle").

Constraint that shapes the whole design
--------------------------------------
The Gemini Live API **cannot** swap tools mid-session. `BidiGenerateContentSetup`
is documented as "sent in the first (and only in the first) message", and a
second `setup` frame mid-stream closes the socket with the very same
``invalid argument`` error we are trying to avoid. (Verified against
https://ai.google.dev/api/live and the SDK: `AsyncSession` exposes only
``send_client_content`` / ``send_realtime_input`` / ``send_tool_response``.)

So "retrieve the right k tools per turn and re-send them" is not an option on
this transport. The engine therefore has to make the *advertised* surface small
and constant, and give the model a way to reach the rest on demand:

* **Fixed-size advertised surface.** A small set of *pinned* tools plus three
  meta-tools is sent to Gemini. Token cost and the 128 ceiling are both
  constant regardless of how many tools are installed.
* **The rest of the catalog lives behind discovery.** ``search_tools`` (rank
  the catalog against an intent) and ``call_tool`` (execute by name) let the
  model reach any of the thousands of remaining tools. ``get_skill_manual``
  pulls a server's usage guide only when it is actually needed.

Retrieval is deliberately **self-contained**: no external vector database, no
local LLM router (this machine is CPU-only, where even a 3B model takes
minutes per turn), no mandatory network dependency. Three scorers run locally
and are fused with Reciprocal Rank Fusion:

* ``fts5``  — SQLite FTS5/BM25 over the catalog (stdlib, ~1 ms, always on).
* ``ngram`` — hashed word-1/2-gram + char-4-gram TF-IDF cosine in pure
  numpy (handles verb/noun phrasing variance BM25 misses, e.g.
  "dim the monitors" -> ``system_diagnostics``).
* ``embed`` — optional micro-embeddings via ``sentence-transformers``
  ``all-MiniLM-L6-v2`` (~22 MB, 15-30 ms on CPU). Auto-detected; when absent
  the engine silently runs on the other two and logs which backend it used.
  Never a hard requirement.

Embeddings are cached on disk keyed by a catalog fingerprint, so adding or
editing a tool re-embeds only the changed rows.

Design rules
------------
* Fail-soft everywhere. A retrieval or cache failure degrades to
  "advertise more tools", never to a crash.
* Provider-neutral. The engine only ever produces
  ``(declarations, skill_context)``; it never talks to Gemini itself.
* Single process-wide instance (:func:`get_index`) so the catalog is built once
  per process and refreshed on demand after MCP servers connect.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("ToolIndex")

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Gemini Live rejects a handshake with more than this many declarations.
GEMINI_MAX_TOOLS = 128

#: We stay comfortably below the ceiling so a user adding a few small MCP
#: servers cannot push us over the edge by surprise.
DEFAULT_ADVERTISED_BUDGET = 24

#: Tools always advertised, whatever the user asks about. Keep this list short:
#: every entry here is permanent per-session token cost.
DEFAULT_PINNED_TOOLS = (
    "undo",
    "recall_memory",
)

#: The three discovery primitives. Always advertised whenever the index is
#: enabled — without them the unadvertised catalog is unreachable.
META_SEARCH_TOOL = "search_tools"
META_CALL_TOOL = "call_tool"
META_MANUAL_TOOL = "get_skill_manual"
META_TOOLS = (META_SEARCH_TOOL, META_CALL_TOOL, META_MANUAL_TOOL)

#: Reciprocal Rank Fusion constant. 60 is the value from the original RRF paper.
RRF_K = 60

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SERVER_PREFIX_RE = re.compile(r"^mcp[_-](?P<server>[^_]+(?:_[^_]+)*?)_(?P<tool>[a-z0-9_]+)$")

# Generic English filler that carries no routing signal. Dropped before
# indexing/scoring so it cannot dilute TF-IDF weights.
_STOPWORDS = frozenset("""
a an and are as at be been being but by can could did do does doing done for
from had has have having he her here hers him his how i if in into is it its
just me my of on or our out over own please she should so some such than that
the their them then there these they this those to too us was we were what
when where which who whom why will with would you your
get got make made use used using want need like also any all more most some
""".split())


# --------------------------------------------------------------------------- #
# Routing metadata overrides
# --------------------------------------------------------------------------- #

#: Ships a small set of routing keywords for hand-written native tools whose
#: descriptions are too thin to route on ("web_search" is described only as
#: "Searches the web for any information", so a request about Reddit matches
#: nothing). This file is the user-editable knob: add or correct entries here
#: and the next index rebuild picks them up, with no code change. MCP tools do
#: not need it — their descriptions come from the server.
DEFAULT_OVERRIDES: Dict[str, Dict[str, str]] = {
    "web_search": {
        "keywords": "internet online website browse browsing reddit twitter google "
                    "news article url page query lookup find information",
    },
    "computer_settings": {
        "keywords": "brightness dim screen display monitor volume mute wifi network "
                    "dark mode fullscreen zoom wallpaper",
    },
    "computer_control": {
        "keywords": "click type press key mouse keyboard focus window app automate",
    },
    "browser_control": {
        "keywords": "web page tab navigate url click form submit scroll",
    },
    "desktop_control": {
        "keywords": "desktop window launch minimise maximise snap taskbar",
    },
}


def load_overrides(path: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """Reads user routing overrides, merged over the shipped defaults.

    Stored as ``{"tool_name": {"keywords": "...", "domain": "..."}}``. A file
    that fails to parse is ignored with a warning: bad metadata must never stop
    the agent from starting.
    """
    merged: Dict[str, Dict[str, str]] = {k: dict(v) for k, v in DEFAULT_OVERRIDES.items()}
    target = path or (Path(os.getenv("LOCALAPPDATA", "~")) / "BrahmaAI" / "tool_routing.json")
    if not target.exists():
        return merged
    try:
        with open(target, "r", encoding="utf-8") as handle:
            user = json.load(handle)
        if isinstance(user, dict):
            for name, meta in user.items():
                if isinstance(meta, dict):
                    slot = merged.setdefault(str(name), {})
                    for field in ("keywords", "domain"):
                        value = meta.get(field)
                        if isinstance(value, str) and value.strip():
                            slot[field] = value.strip()
    except Exception as exc:
        logger.warning("[ToolIndex] Ignoring unreadable routing overrides at %s: %s", target, exc)
    return merged


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass
class ToolEntry:
    """One indexable tool.

    ``skill_id`` groups tools that share a usage guide (one MCP server, one
    native feature family) so a whole group's manual is fetched once instead of
    per tool. ``keywords`` and ``domain`` are routing aids that do not require
    the upstream server to cooperate — they are inferred from the name/source
    when the server provides nothing better.
    """

    name: str
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    #: "native" | "skill" | "mcp:<server>"
    source: str = "native"
    #: Free-form grouping key for manuals (defaults to ``source``).
    skill_id: str = ""
    #: Human-facing domain label used to disambiguate near-duplicate verbs
    #: ("Automate a web page in Chromium" vs "Automate a native Windows app").
    domain: str = ""
    #: Extra retrieval terms, space separated.
    keywords: str = ""
    #: Never dropped by retrieval; always advertised.
    pinned: bool = False
    #: Function declaration exactly as it should be sent to the provider.
    declaration: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.skill_id:
            self.skill_id = self.source
        if not self.domain:
            self.domain = _infer_domain(self.source, self.name)
        if not self.declaration:
            self.declaration = {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "OBJECT", "properties": {}},
            }

    def apply_routing(self, meta: Dict[str, str]) -> "ToolEntry":
        """Merges routing metadata (keywords/domain) onto this entry.

        Used for the override file and for metadata an MCP server does not
        supply. Only routing fields are touched — the provider-facing
        declaration is left exactly as the upstream server described it.
        """
        if not isinstance(meta, dict):
            return self
        keywords = meta.get("keywords")
        if isinstance(keywords, str) and keywords.strip():
            self.keywords = keywords.strip()
        domain = meta.get("domain")
        if isinstance(domain, str) and domain.strip():
            self.domain = domain.strip()
        return self

    # -- derived ----------------------------------------------------------- #

    @property
    def server(self) -> str:
        """MCP server name for ``mcp:*`` sources, else the bare source."""
        return self.source[4:] if self.source.startswith("mcp:") else self.source

    def search_text(self) -> str:
        """The single blob indexed and embedded for this tool.

        Name tokens are repeated so an exact tool-name mention (users say
        "calyx_search" out loud) outranks an incidental description word.
        """
        name_tokens = " ".join(_name_variants(self.name))
        param_names = " ".join(_parameter_names(self.parameters))
        parts = [
            name_tokens,
            name_tokens,
            name_tokens,
            self.name.replace("_", " "),
            self.domain.replace("_", " "),
            self.keywords.replace("_", " "),
            self.description,
            param_names,
        ]
        return " ".join(p for p in parts if p)

    def short_description(self, limit: int = 220) -> str:
        """Description trimmed for embedding — trailing prose adds noise."""
        text = " ".join((self.description or "").split())
        if len(text) <= limit:
            return text
        cut = text[:limit].rsplit(" ", 1)[0]
        return cut + "…"


@dataclass
class SkillManual:
    """A usage guide for one ``skill_id``.

    Populated automatically from an MCP server's ``initialize`` instructions and
    ``prompts/list`` + ``prompts/get``, or authored locally. Never sent to the
    model unless a tool from the same ``skill_id`` is actually advertised.
    """

    skill_id: str
    title: str = ""
    body: str = ""
    #: Where this came from: "mcp:<server>" | "settings" | "file:<path>"
    origin: str = ""
    updated_at: float = 0.0

    def rendered(self, max_chars: int = 4000) -> str:
        header = self.title.strip() or self.skill_id
        body = (self.body or "").strip()
        if not body:
            return ""
        if len(body) > max_chars:
            body = body[:max_chars].rsplit(" ", 1)[0] + "\n… (truncated)"
        return f"### {header}\n{body}"


@dataclass
class SearchHit:
    """One ranked retrieval result."""

    entry: ToolEntry
    score: float
    #: Per-backend ranks, for explain/debug surfaces.
    ranks: Dict[str, int] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.entry.name


@dataclass
class IndexStats:
    """Diagnostics for the UI/logs. Never required for correctness."""

    tools: int = 0
    manuals: int = 0
    backends: List[str] = field(default_factory=list)
    embed_model: str = ""
    last_build_ms: float = 0.0
    last_query_ms: float = 0.0
    cached_vectors: int = 0
    catalog_fingerprint: str = ""
    enabled: bool = True
    advertised: int = 0
    total_tokens_estimate: int = 0


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #


def _tokens(text: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS and len(t) > 1]


def _name_variants(name: str) -> List[str]:
    """``mcp_Synapse_target_act`` -> raw, split, and expanded segments.

    Feeds the matcher both the exact identifier (so a spoken tool name hits
    hard) and its component words (``target``, ``act``) so a natural-language
    request still lands on it.
    """
    out = [name]
    parts = [p for p in re.split(r"[_\-.:]+", name) if p]
    if len(parts) > 1:
        out.append(" ".join(parts))
        out.extend(parts)
    return out


def _parameter_names(parameters: Any) -> List[str]:
    """Top-level parameter names, which are strong routing hints."""
    names: List[str] = []
    if isinstance(parameters, dict):
        props = parameters.get("properties")
        if isinstance(props, dict):
            names.extend(str(k).replace("_", " ") for k in props)
    return names[:12]


def _infer_domain(source: str, name: str) -> str:
    """Best-effort domain label when the upstream server gives us nothing.

    Deliberately coarse. This exists solely to break ties between
    near-duplicate verbs (four different ways to click something).
    """
    if source.startswith("mcp:"):
        return f"mcp {source[4:].replace('_', ' ')}"
    lowered = name.lower()
    for needle, label in (
        ("browser", "browser"), ("chrome", "browser"), ("web", "web"),
        ("mcp", "mcp server"), ("spotify", "spotify"), ("audio", "audio"),
        ("system", "system"), ("window", "desktop window"), ("file", "files"),
    ):
        if needle in lowered:
            return label
    return source.replace("_", " ")


def _split_clauses(text: str, limit: int = 4) -> List[str]:
    """Split a compound request into independent intents.

    "Search the web for NVDA and then build a PowerPoint about it" must retrieve
    for *both* halves; scoring the whole sentence once starves whichever concept
    contributes fewer words. Coordination and sequencing connectives are the
    split points, but only when both sides carry real content.
    """
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(
        r"\s*(?:;|, then\b|\band then\b|\bthen\b|\bafter that\b|\balso\b|\band\b|\bplus\b)\s*",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = [p.strip(" .?!,") for p in parts if p and len(p.strip()) > 2]
    clauses = [c for c in cleaned if _tokens(c)]
    if not clauses:
        return [text]
    return clauses[:limit]


# --------------------------------------------------------------------------- #
# Backend: SQLite FTS5 / BM25
# --------------------------------------------------------------------------- #


class _Fts5Backend:
    """BM25 keyword retrieval backed by an in-memory SQLite FTS5 index.

    FTS5 ships with CPython's bundled SQLite on every platform Brahma targets,
    so this backend is always available and needs no extra dependency. It is the
    safety net: if the optional embedding backend is missing or fails, keyword
    retrieval still keeps the catalog reachable.
    """

    name = "fts5"

    def __init__(self) -> None:
        self._conn: Optional[sqlite3.Connection] = None
        self._rows: List[Tuple[int, str, str]] = []
        self.available = False

    def build(self, entries: Sequence[ToolEntry]) -> None:
        self._rows = []
        self._conn = None
        self.available = False
        if not entries:
            return
        try:
            conn = sqlite3.connect(":memory:")
            conn.execute(
                "CREATE VIRTUAL TABLE tools USING fts5("
                "  name, body, tokenize='unicode61 remove_diacritics 2')"
            )
            rows = []
            for idx, entry in enumerate(entries):
                rows.append((idx, entry.name, entry.search_text()))
            conn.executemany("INSERT INTO tools(rowid, name, body) VALUES (?, ?, ?)", rows)
            conn.commit()
        except sqlite3.Error as exc:
            # FTS5 not compiled in — degrade to "this backend is unavailable".
            logger.warning("[ToolIndex] FTS5 unavailable, keyword backend disabled: %s", exc)
            return
        self._conn = conn
        self._rows = rows
        self.available = True

    def rank(self, query: str, k: int) -> List[Tuple[str, float]]:
        if self._conn is None or not query:
            return []
        # Quote each token so user speech containing FTS operators (", "*", "-")
        # cannot produce a syntax error mid-conversation.
        terms = [f'"{t}"' for t in _tokens(query)[:24]]
        if not terms:
            return []
        match = " OR ".join(terms)
        try:
            cur = self._conn.execute(
                "SELECT rowid, bm25(tools) FROM tools WHERE tools MATCH ? "
                "ORDER BY bm25(tools) LIMIT ?",
                (match, max(k * 4, 20)),
            )
            rows = cur.fetchall()
        except sqlite3.Error as exc:
            logger.debug("[ToolIndex] FTS5 query failed (%s) for %r", exc, query)
            return []
        # bm25() returns "smaller is better"; negate so every backend's score is
        # a larger-is-better affinity before RRF fusion.
        return [(self._rows[r][1], -float(score)) for r, score in rows]

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None


# --------------------------------------------------------------------------- #
# Backend: hashed n-gram TF-IDF (numpy cosine)
# --------------------------------------------------------------------------- #


class _NgramBackend:
    """Hashed word+char n-gram TF-IDF cosine, computed with numpy.

    BM25 rewards literal token overlap, so it misses paraphrase: "dim the
    monitors" shares no token with "system brightness diagnostics". Character
    4-grams and word bigrams recover a useful amount of that, and word-level
    sublinear TF plus IDF weighting is what keeps it from drowning in the long
    MCP descriptions. Cost is a few milliseconds for thousands of tools.
    """

    name = "ngram"
    _DIM = 4096

    def __init__(self) -> None:
        self._matrix: Any = None  # numpy (n_docs, dim) L2-normalized
        self._names: List[str] = []
        self._index: Dict[str, int] = {}
        self.available = False

    @staticmethod
    def _features(text: str) -> Dict[int, float]:
        lowered = (text or "").lower()
        toks = _TOKEN_RE.findall(lowered)
        feats: Dict[int, float] = {}

        def _add(feature: str, weight: float) -> None:
            h = hashlib.blake2b(feature.encode("utf-8"), digest_size=4).digest()
            slot = int.from_bytes(h, "little") % _NgramBackend._DIM
            feats[slot] = feats.get(slot, 0.0) + weight

        for tok in toks:
            if tok in _STOPWORDS or len(tok) < 2:
                continue
            _add(f"w:{tok}", 1.0)
        for a, b in zip(toks, toks[1:]):
            if a not in _STOPWORDS and b not in _STOPWORDS:
                _add(f"b:{a}_{b}", 1.4)
        # Char 4-grams over the compacted token stream give fuzzy tolerance to
        # plurals and typos without needing a stemmer.
        compact = " ".join(toks)
        padded = f" {compact} "
        for i in range(max(0, len(padded) - 3)):
            gram = padded[i : i + 4]
            if gram.strip():
                _add(f"c:{gram}", 0.25)
        return feats

    def build(self, entries: Sequence[ToolEntry]) -> None:
        self.available = False
        self._names = []
        self._index = {}
        if not entries:
            return
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover - numpy is in requirements.txt
            logger.warning("[ToolIndex] numpy unavailable, n-gram backend disabled: %s", exc)
            return

        n = len(entries)
        dim = self._DIM
        counts = np.zeros((n, dim), dtype=np.float32)
        doc_freq = np.zeros(dim, dtype=np.float32)

        for row, entry in enumerate(entries):
            feats = self._features(entry.search_text())
            for slot, raw in feats.items():
                # Sublinear TF: a term repeated 20x in a rambling description is
                # not 20x more relevant.
                counts[row, slot] = 1.0 + math.log(raw) if raw > 1.0 else raw
            for slot in feats:
                doc_freq[slot] += 1.0
            self._names.append(entry.name)
            self._index[entry.name] = row

        # Smoothed IDF. Rare discriminative terms dominate, as intended.
        idf = np.log((1.0 + n) / (1.0 + doc_freq)) + 1.0
        counts *= idf
        norms = np.linalg.norm(counts, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        self._matrix = counts / norms
        self.available = True

    def rank(self, query: str, k: int) -> List[Tuple[str, float]]:
        if self._matrix is None or not query:
            return []
        try:
            import numpy as np
        except ImportError:
            return []
        feats = self._features(query)
        if not feats:
            return []
        vec = np.zeros(self._matrix.shape[1], dtype=np.float32)
        for slot, raw in feats.items():
            vec[slot] = 1.0 + math.log(raw) if raw > 1.0 else raw
        # Query-side IDF is intentionally omitted: at query time we have no
        # corpus statistics cheap enough to be worth it, and the char-grams
        # already supply the fuzzy signal.
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            return []
        scores = self._matrix @ (vec / norm)
        top = min(max(k * 4, 20), len(self._names))
        # argpartition is O(n); a full sort would not matter here but this keeps
        # the intent explicit.
        candidates = np.argpartition(-scores, top - 1)[:top]
        ordered = sorted(candidates, key=lambda i: float(scores[i]), reverse=True)
        return [
            (self._names[i], float(scores[i]))
            for i in ordered
            if float(scores[i]) > 0.0
        ]


# --------------------------------------------------------------------------- #
# Backend: optional micro-embeddings
# --------------------------------------------------------------------------- #

#: Small, CPU-fast sentence embedder. Chosen for size (~22 MB) and latency
#: rather than peak quality: the job is routing a few thousand short tool
#: descriptions, not semantic search over prose.
#:
#: Measured on the target CPU-only host (see docs/ONE-391-tool-index.md):
#: ~0.2 s model load, ~5 ms/tool to index (173 tools ~= 0.9 s cold), and
#: ~2.9 ms mean / 3.6 ms p95 per query. That is fast enough to sit in the
#: request path, and 1-2 orders of magnitude cheaper than routing through a
#: local LLM (minutes per turn on this hardware).
DEFAULT_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


_model_lock = threading.Lock()
_model_cache: Dict[str, Tuple[Any, str]] = {}


def _load_shared_model(model_name: str) -> Optional[Tuple[Any, str]]:
    """Loads an embedding model once per process.

    Rebuilds are routine (every MCP server that connects triggers one), and
    re-instantiating an ONNX session each time wastes both time and memory.
    Returns ``(model, runtime)`` or ``None`` when no runtime is installed.
    """
    with _model_lock:
        cached = _model_cache.get(model_name)
        if cached is not None:
            return cached

        # -- fastembed (ONNX, preferred): ~30 MB, no torch ---------------- #
        try:
            from fastembed import TextEmbedding  # type: ignore

            model = TextEmbedding(model_name)
            _model_cache[model_name] = (model, "fastembed")
            logger.info("[ToolIndex] Semantic backend active via ONNX (%s).", model_name)
            return _model_cache[model_name]
        except ImportError:
            pass
        except Exception as exc:
            logger.warning("[ToolIndex] fastembed load failed (%s); trying torch runtime.", exc)

        # -- sentence-transformers (torch, fallback) ---------------------- #
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            model = SentenceTransformer(model_name)
            _model_cache[model_name] = (model, "sentence-transformers")
            logger.info("[ToolIndex] Semantic backend active via torch (%s).", model_name)
            return _model_cache[model_name]
        except ImportError:
            logger.info(
                "[ToolIndex] No semantic backend installed; running on keyword + n-gram "
                "retrieval only. Paraphrase routing will be weaker. Enable with: "
                "pip install fastembed"
            )
            return None
        except Exception as exc:
            logger.warning("[ToolIndex] Could not load embedding model %s: %s", model_name, exc)
            return None


class _EmbedBackend:
    """Optional true-semantic scorer using a local micro-embedding model.

    Two runtimes are supported, in preference order:

    * ``fastembed`` — ONNX Runtime, no torch. Preferred: the install is ~30 MB
      and imports in well under a second. This is what we benchmark.
    * ``sentence-transformers`` — the reference runtime, kept as a fallback for
      anyone who already has torch installed.

    Neither is required. Without either, the backend reports itself unavailable
    and the engine runs on FTS5 + n-gram alone. That configuration is correct
    and fast, just weaker on paraphrase ("dim the monitors" -> brightness).
    """

    name = "embed"

    def __init__(self, model_name: str = DEFAULT_EMBED_MODEL) -> None:
        self.model_name = model_name
        self._model: Any = None
        self._runtime = ""
        self._matrix: Any = None
        self._names: List[str] = []
        self._index: Dict[str, int] = {}
        self.available = False
        #: Guards the model session against a concurrent rebuild racing the
        #: ONNX runtime. Must be reentrant: ``build`` calls ``load`` while
        #: holding it. Deliberately never held while taking the index lock —
        #: see :meth:`schedule_build` for why.
        self._lock = threading.RLock()
        #: Separate, single-purpose lock for the in-flight build flag. Keeping
        #: it distinct is what makes the lock ordering provably acyclic.
        self._flag_lock = threading.Lock()
        #: True while a background semantic build is in flight.
        self._building = False

    @property
    def runtime(self) -> str:
        return self._runtime

    def load(self) -> bool:
        """Loads a semantic model. Returns False (never raises) if unavailable."""
        with self._lock:
            if self.available:
                return True
            loaded = _load_shared_model(self.model_name)
            if loaded is None:
                return False
            self._model, self._runtime = loaded
            self.available = True
            return True

    def _encode(self, texts: Sequence[str]) -> Any:
        """Encodes with whichever runtime loaded. Returns a float32 matrix."""
        import numpy as np

        if self._runtime == "fastembed":
            return np.asarray(list(self._model.embed(list(texts))), dtype=np.float32)
        encoded = self._model.encode(
            list(texts), normalize_embeddings=True, show_progress_bar=False
        )
        return np.asarray(encoded, dtype=np.float32)

    def build(self, entries: Sequence[ToolEntry], cache: "_EmbedCache") -> None:
        with self._lock:
            if not entries or not self.load():
                self.available = False
                self._names = []
                self._index = {}
                return
            try:
                import numpy as np  # noqa: F401
            except ImportError:
                self.available = False
                return

            names = [e.name for e in entries]
            fingerprint = cache.fingerprint(names)
            vectors = cache.get(fingerprint, len(names))
            if vectors is None:
                texts = [
                    f"{e.name.replace('_', ' ')}. {e.short_description(200)}. {e.domain} {e.keywords}"
                    for e in entries
                ]
                vectors = self._encode(texts)
                cache.put(fingerprint, vectors)

            # Row order must match catalog order so MMR can index by name.
            self._matrix = vectors
            self._names = names
            self._index = {n: i for i, n in enumerate(names)}
            self.available = True

    def schedule_build(
        self,
        entries: Sequence[ToolEntry],
        cache: "_EmbedCache",
        on_ready: Optional[Callable[[], None]] = None,
    ) -> None:
        """Builds the semantic index on a daemon thread.

        Loading the model and embedding a few hundred descriptions is fast but
        not instant, and it must never sit in front of a Live handshake. Until
        the thread finishes, :attr:`available` stays False and retrieval runs on
        the keyword backends alone. A rebuild requested while one is in flight
        is dropped; the newer catalog is picked up on the next rebuild.

        Lock ordering (this matters). ``ToolIndex.rebuild`` runs while holding
        the index lock, so this method must not take the model lock — it uses a
        dedicated flag lock instead. The worker takes the model lock inside
        ``build()``, releases it, and only then calls ``on_ready`` which takes
        the index lock. So the worker never holds two locks at once and the
        cycle index->model / model->index cannot form. Holding the model lock
        across ``on_ready`` deadlocks the process on the second rebuild.

        The model lock itself is an ``RLock`` because ``build`` calls ``load``
        while already holding it; a plain ``Lock`` self-deadlocks there and the
        semantic backend silently never activates.
        """
        if not entries:
            return
        with self._flag_lock:
            if self._building:
                return
            self._building = True

        def _work() -> None:
            try:
                self.build(entries, cache)
            except Exception as exc:
                logger.warning("[ToolIndex] Semantic indexing failed, continuing keyword-only: %s", exc)
            finally:
                # build() has released the model lock by this point.
                with self._flag_lock:
                    self._building = False
                if on_ready is not None:
                    try:
                        on_ready()
                    except Exception:
                        pass

        threading.Thread(target=_work, name="BrahmaToolIndexEmbed", daemon=True).start()

    def rank(self, query: str, k: int) -> List[Tuple[str, float]]:
        if self._matrix is None or not query:
            return []
        try:
            import numpy as np

            vec = self._encode([query])[0]
        except Exception as exc:
            logger.warning("[ToolIndex] Embedding query failed, semantic backend disabled: %s", exc)
            self.available = False
            return []
        scores = (self._matrix @ vec).reshape(-1)
        top = min(max(k * 4, 20), len(self._names))
        ordered = np.argsort(-scores)[:top]
        return [(self._names[i], float(scores[i])) for i in ordered if float(scores[i]) > 0.0]

    def vectors_for(self, entries: Sequence[ToolEntry]) -> Any:
        """Embedding matrix for already-built entries (used for MMR diversity)."""
        return self._matrix

    def snapshot(self, names: Sequence[str]) -> Optional[Tuple[Any, Dict[str, int]]]:
        """Returns ``(matrix, index)`` only if it covers exactly these names.

        MCP servers connect on a background loop, so a rebuild can swap the
        matrix and its row index out from under an in-flight query. Handing back
        a consistent pair — or ``None`` when the caller's names are not all
        present — lets the caller fall back instead of raising a KeyError or
        silently comparing against the wrong rows.
        """
        with self._lock:
            if self._matrix is None:
                return None
            index = self._index
            if any(n not in index for n in names):
                return None
            return self._matrix, index


class _EmbedCache:
    """Disk cache of embedding matrices keyed by a catalog fingerprint.

    Embedding a few thousand descriptions on CPU can take tens of seconds; the
    cache makes startup sub-millisecond in the common case where nothing
    changed. Only re-embeds when the set of indexed tool names changes, and
    stores per-name vectors so a single added tool does not invalidate the rest.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @staticmethod
    def fingerprint(names: Sequence[str]) -> str:
        joined = "\n".join(sorted(names))
        return hashlib.blake2b(joined.encode("utf-8"), digest_size=16).hexdigest()

    def get(self, fingerprint: str, expected: int) -> Any:
        path = self.directory / f"{fingerprint}.npy"
        if not path.exists():
            return None
        try:
            import numpy as np

            arr = np.load(path)
            if arr.shape[0] != expected:
                return None
            return np.asarray(arr, dtype=np.float32)
        except Exception:
            return None

    def put(self, fingerprint: str, vectors: Any) -> None:
        with self._lock:
            path = self.directory / f"{fingerprint}.npy"
            tmp = path.with_suffix(".npy.tmp")
            try:
                import numpy as np

                np.save(tmp, vectors)
                os.replace(tmp, path)
            except Exception as exc:
                logger.debug("[ToolIndex] Embedding cache write failed: %s", exc)
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass


# --------------------------------------------------------------------------- #
# The index
# --------------------------------------------------------------------------- #


class ToolIndex:
    """Process-wide catalog of every tool plus hybrid retrieval over it.

    Thread safety: :meth:`rebuild` and :meth:`search` hold a re-entrant lock.
    MCP servers finish connecting on a background loop *after* the index is
    first built, so refreshes are expected during normal operation.
    """

    def __init__(
        self,
        *,
        cache_dir: Optional[Path] = None,
        embed_model: str = DEFAULT_EMBED_MODEL,
        enabled: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        self._entries: Dict[str, ToolEntry] = {}
        self._manuals: Dict[str, SkillManual] = {}
        self._order: List[str] = []
        self._fingerprint: str = ""
        self._built = False
        self._enabled = enabled
        self._diversity_ready = False
        #: Tool names the user has switched off. Disabled tools stay in
        #: ``_entries`` (so toggling back on is instant and a catalog resync
        #: cannot lose them) but are invisible to retrieval, advertisement and
        #: execution — i.e. fully removed from what the model can see or call.
        self._disabled: set[str] = set()

        self._fts = _Fts5Backend()
        self._ngram = _NgramBackend()
        self._embed = _EmbedBackend(embed_model)
        self._embed_cache = _EmbedCache(
            cache_dir or (Path(os.getenv("LOCALAPPDATA", "~")) / "BrahmaAI" / "tool_index_cache")
        )
        self._overrides = load_overrides()
        self._stats = IndexStats(enabled=enabled)

    # -- catalog ----------------------------------------------------------- #

    def upsert(self, entry: ToolEntry) -> None:
        """Adds or replaces one tool. Cheap; the index rebuilds lazily."""
        with self._lock:
            entry.apply_routing(self._overrides.get(entry.name, {}))
            if entry.name in self._entries:
                # Preserve a pin the caller did not know about.
                entry.pinned = entry.pinned or self._entries[entry.name].pinned
            self._entries[entry.name] = entry
            if entry.name not in self._order:
                self._order.append(entry.name)
            self._built = False

    def upsert_many(self, entries: Iterable[ToolEntry]) -> int:
        count = 0
        for entry in entries:
            self.upsert(entry)
            count += 1
        return count

    def remove_source(self, source: str) -> int:
        """Drops every tool from one source (e.g. an MCP server that died)."""
        with self._lock:
            doomed = [n for n, e in self._entries.items() if e.source == source]
            for name in doomed:
                self._entries.pop(name, None)
                if name in self._order:
                    self._order.remove(name)
            self._manuals.pop(source, None)
            self._built = False
            return len(doomed)

    def remove_skill(self, skill_id: str) -> None:
        with self._lock:
            self._manuals.pop(skill_id, None)
            for name in [n for n, e in self._entries.items() if e.skill_id == skill_id]:
                self._entries.pop(name, None)
                if name in self._order:
                    self._order.remove(name)
            self._built = False

    def put_manual(self, manual: SkillManual) -> None:
        with self._lock:
            manual.updated_at = manual.updated_at or time.time()
            self._manuals[manual.skill_id] = manual

    def manual(self, skill_id: str) -> Optional[SkillManual]:
        with self._lock:
            manual = self._manuals.get(skill_id)
            # Accept a bare server name as an alias for its "mcp:<server>" group.
            if manual is None and not skill_id.startswith("mcp:"):
                manual = self._manuals.get(f"mcp:{skill_id}")
            return manual

    def manuals(self) -> List[SkillManual]:
        with self._lock:
            return list(self._manuals.values())

    def entries(self) -> List[ToolEntry]:
        """Visible tools only; disabled tools are omitted."""
        with self._lock:
            return [
                self._entries[n]
                for n in self._order
                if n in self._entries and n not in self._disabled
            ]

    def all_entries(self) -> List[ToolEntry]:
        """Every catalogued tool, including disabled ones (for the settings UI)."""
        with self._lock:
            return [self._entries[n] for n in self._order if n in self._entries]

    def get(self, name: str) -> Optional[ToolEntry]:
        with self._lock:
            if name in self._disabled:
                return None
            return self._entries.get(name)

    def skill_ids(self) -> List[str]:
        with self._lock:
            return sorted({e.skill_id for e in self._entries.values()})

    # -- build ------------------------------------------------------------- #

    def rebuild(self, force: bool = False) -> IndexStats:
        """(Re)build the retrieval structures. Cheap no-op when unchanged."""
        with self._lock:
            if self._built and not force:
                return self._stats
            started = time.perf_counter()
            entries = [
                self._entries[n]
                for n in self._order
                if n in self._entries and n not in self._disabled
            ]
            self._fingerprint = _EmbedCache.fingerprint([e.name for e in entries])

            self._fts.build(entries)
            self._ngram.build(entries)
            # Semantic indexing is the only expensive step (~0.9s for 173 tools,
            # ~5s for 1000). Never block a Live handshake on it: build the fast
            # keyword structures now, warm the semantic one in the background.
            # Until it lands, retrieval runs on FTS5 + n-gram alone.
            self._embed.schedule_build(entries, self._embed_cache, on_ready=self._on_embed_ready)
            self._diversity_ready = False

            active = [b.name for b in (self._fts, self._ngram) if b.available]
            if self._embed.available:
                active.append("embed")
            if not active:
                logger.warning("[ToolIndex] No retrieval backend available; discovery will be lexical-name only.")

            self._built = True
            self._stats = IndexStats(
                tools=len(entries),
                manuals=len(self._manuals),
                backends=active,
                embed_model=self._embed.model_name if self._embed.available else "",
                last_build_ms=round((time.perf_counter() - started) * 1000.0, 2),
                enabled=self._enabled,
                catalog_fingerprint=self._fingerprint,
            )
            logger.info(
                "[ToolIndex] Indexed %d tool(s) in %.1f ms via %s",
                len(entries),
                self._stats.last_build_ms,
                ", ".join(active) or "no backend",
            )
            return self._stats

    def _on_embed_ready(self) -> None:
        """Folds the warmed semantic backend into the reported backend list."""
        with self._lock:
            active = [b.name for b in (self._fts, self._ngram) if b.available]
            if self._embed.available:
                active.append("embed")
                self._stats.backends = active
                self._stats.embed_model = f"{self._embed.model_name} ({self._embed.runtime})"
                logger.info("[ToolIndex] Semantic backend ready; active backends: %s", ", ".join(active))

    # -- search ------------------------------------------------------------ #

    def search(self, query: str, k: int = 8) -> List[SearchHit]:
        """Rank the catalog against an intent using RRF-fused backends.

        Multi-clause requests are scored per clause and fused, so a compound
        ask ("search the web for NVDA, then build a deck") surfaces tools for
        both halves instead of only the dominant one.
        """
        started = time.perf_counter()
        clauses = _split_clauses(query)
        if not clauses:
            return []

        self.rebuild()
        fused: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            backends = [b for b in (self._fts, self._ngram, self._embed) if b.available]
            k = max(1, int(k))
            for clause in clauses:
                per_backend: List[List[Tuple[str, float]]] = []
                for backend in backends:
                    try:
                        per_backend.append(backend.rank(clause, k))
                    except Exception as exc:
                        logger.debug("[ToolIndex] Backend %s failed: %s", backend.name, exc)
                        per_backend.append([])
                per_backend.extend(self._lexical_fallback(clause, k))

                for results in per_backend:
                    for rank, (name, _score) in enumerate(results, start=1):
                        slot = fused.setdefault(name, {"rrf": 0.0, "ranks": {}})
                        slot["rrf"] += 1.0 / (RRF_K + rank)
                        slot["ranks"].setdefault("rank", rank)

            hits: List[SearchHit] = []
            for name, slot in fused.items():
                if name in self._disabled:
                    continue
                entry = self._entries.get(name)
                if entry is None:
                    continue
                hits.append(SearchHit(entry=entry, score=slot["rrf"], ranks=dict(slot["ranks"])))

            # Exact-identifier boost. If the user says a tool name outright, that
            # tool wins — full stop. RRF alone cannot express this: a name like
            # "synapse_browser_wait_for_url" shares most of its n-grams with
            # "synapse_browser_wait_for", so pure rank fusion ranks the shorter
            # sibling first. Users naming a tool expect that tool.
            needle = re.sub(r"[^a-z0-9_]", "", query.lower())
            if needle:
                for hit in hits:
                    flat = hit.name.lower()
                    if flat == needle:
                        hit.score += 1.0
                    elif len(flat) > 5 and flat in needle:
                        hit.score += 0.5

        hits.sort(key=lambda h: h.score, reverse=True)
        diversified = self._diversify(hits, k)
        self._stats.last_query_ms = round((time.perf_counter() - started) * 1000.0, 2)
        return diversified

    def _lexical_fallback(self, clause: str, k: int) -> List[List[Tuple[str, float]]]:
        """Exact-identifier safety net.

        Guarantees a tool is reachable when the user names it outright ("run
        calyx_search") even if every learned backend is unavailable. Matches
        both the whole clause and individual spans, because a spoken name is
        usually embedded in a sentence ("please run mcp_Calyx_kernel_answer").
        """
        flat_clause = re.sub(r"[^a-z0-9_]", "", clause.lower())
        span = re.sub(r"[^a-z0-9]+", " ", clause.lower()).split()
        out: List[Tuple[str, float]] = []
        for name in self._order:
            if name in self._disabled:
                continue
            flat = name.lower()
            score = 0.0
            if flat_clause and flat == flat_clause:
                score = 1.0
            elif flat_clause and flat in flat_clause:
                # Exact tool name spoken inside a longer sentence.
                score = 0.9
            elif any(part == flat for part in span):
                score = 0.8
            elif len(flat) > 5 and flat in flat_clause:
                score = 0.7
            if score:
                out.append((name, score))
        return [out[: max(k, 1)]]

    def _diversify(self, hits: List[SearchHit], k: int) -> List[SearchHit]:
        """Maximal-marginal-relevance re-rank to defeat description collision.

        Without this, a catalogue containing ``browser_click``,
        ``computer_control_click`` and ``mcp_Synapse_target_act`` fills every
        slot with three flavours of "click" and starves the second intent. MMR
        caps how much one near-duplicate neighbourhood can occupy, while
        pinned tools bypass the cap entirely (they are not competing for slots).
        """
        if len(hits) <= k:
            return hits
        snap = self._embed.snapshot([h.name for h in hits]) if self._embed.available else None
        if snap is None:
            # No embedding space, or the catalog moved under us: fall back to a
            # cheap textual similarity on the n-gram feature overlap. Same
            # intent, no new dependency, and never a crash.
            return self._diversify_textual(hits, k)
        matrix, row_of = snap

        import numpy as np

        max_per_source = max(2, (k + 1) // 2)
        selected: List[SearchHit] = []
        remaining = list(hits)
        # Diversity strength: 0.7 keeps relevance dominant while still damping
        # near-duplicates. Tunable without retraining anything.
        lam = 0.7
        while remaining and len(selected) < k:
            best: Optional[SearchHit] = None
            best_val: Optional[float] = None
            for cand in remaining:
                cand_vec = matrix[row_of[cand.name]]
                # Redundancy = worst similarity to anything already chosen, so a
                # candidate duplicates none of the winners.
                redundancy = max(
                    (float(np.dot(cand_vec, matrix[row_of[s.name]])) for s in selected),
                    default=0.0,
                )
                val = lam * cand.score - (1.0 - lam) * max(0.0, redundancy)
                # Same cap as the textual path: no single server may claim the
                # whole advertised surface.
                used = sum(1 for s in selected if s.entry.source == cand.entry.source)
                if used >= max_per_source:
                    val -= 0.35
                if best_val is None or val > best_val:
                    best_val, best = val, cand
            if best is None:
                break
            selected.append(best)
            remaining.remove(best)
        return selected

    def _diversify_textual(self, hits: List[SearchHit], k: int) -> List[SearchHit]:
        """Name-similarity MMR plus a per-source cap.

        Name similarity alone is not enough. Synapse ships fifteen tools whose
        names differ (``browser_click``, ``cdp_open_tab``, ``target_act``) but
        which all answer "control the browser" — pure MMR treats them as
        distinct and fills every slot from one server. The source cap bounds how
        much of the advertised surface any single provider may claim, so a
        second MCP server or a native capability can still be represented.
        """
        max_per_source = max(2, (k + 1) // 2)
        selected: List[SearchHit] = []
        remaining = list(hits)
        lam = 0.7

        def _sim(a: ToolEntry, b: ToolEntry) -> float:
            ta, tb = set(_tokens(a.name)), set(_tokens(b.name))
            if not ta or not tb:
                return 0.0
            jaccard = len(ta & tb) / len(ta | tb)
            # Sibling penalty: same server AND overlapping description wording.
            if a.source == b.source:
                da, db = set(_tokens(a.short_description(200))), set(_tokens(b.short_description(200)))
                if da and db and len(da & db) / len(da | db) > 0.5:
                    return max(jaccard, 0.5)
            return jaccard

        while remaining and len(selected) < k:
            best: Optional[SearchHit] = None
            best_val: Optional[float] = None
            for position, cand in enumerate(remaining):
                used = sum(1 for s in selected if s.entry.source == cand.entry.source)
                penalty = max((_sim(cand.entry, s.entry) for s in selected), default=0.0)
                val = lam * cand.score - (1.0 - lam) * penalty
                # Prefer a candidate from an under-represented source without
                # ever letting relevance collapse: the penalty is capped at 0.35.
                if used >= max_per_source:
                    val -= 0.35
                if best_val is None or val > best_val:
                    best_val, best = val, position
            if best is None:
                break
            selected.append(remaining.pop(best))
        return selected

    # -- advertised surface ------------------------------------------------ #

    def resolve(
        self,
        query: str = "",
        budget: int = DEFAULT_ADVERTISED_BUDGET,
    ) -> Dict[str, Any]:
        """Produce the connection payload: declarations + skill context.

        Returns::

            {
              "declarations": [...],   # provider-ready function declarations
              "skill_context": str,     # JIT manual block for used skill groups
              "hits": [SearchHit, ...], # what retrieval chose, for diagnostics
              "deferred": int,          # tools NOT advertised this session
            }

        ``budget`` counts *all* advertised declarations, pinned and meta
        included, and is clamped so the Gemini ceiling cannot be exceeded even
        if a caller passes something silly.
        """
        with self._lock:
            pinned_entries = sorted(
                (
                    e
                    for e in self._entries.values()
                    if e.pinned and e.name not in self._disabled
                ),
                key=lambda e: self._order.index(e.name) if e.name in self._order else 0,
            )

        pinned_names = {e.name for e in pinned_entries}
        room = max(0, int(budget) - len(pinned_names) - len(META_TOOLS))
        room = min(room, GEMINI_MAX_TOOLS - len(pinned_names) - len(META_TOOLS))

        hits: List[SearchHit] = []
        if room > 0:
            # Over-fetch so MMR has material to choose from.
            hits = self.search(query, k=max(room * 3, room))
            hits = [h for h in hits if h.entry.name not in pinned_names]
            # When there is no query yet (first connect), fall back to a stable,
            # useful ordering rather than an arbitrary one: broad-coverage
            # built-ins first, then everything else alphabetically.
            if not query.strip() and len(self.entries()) > len(pinned_names):
                hits = self._cold_start_order(room)
            hits = hits[:room]

        selected = list(pinned_entries) + [h.entry for h in hits]
        self._stats.advertised = len(selected)
        self._stats.total_tokens_estimate = _estimate_tokens(selected)

        skill_ids = {e.skill_id for e in selected} - {"native"}
        context = self.render_skill_context(sorted(skill_ids))
        total = len(self.entries())
        return {
            "declarations": [e.declaration for e in selected],
            "skill_context": context,
            "hits": hits,
            "pinned": pinned_names,
            "deferred": max(0, total - len(selected)),
        }

    def _cold_start_order(self, room: int) -> List[SearchHit]:
        """Deterministic pre-query selection.

        At connect time there is no user intent yet. Rather than embed a
        meaningless empty string, advertise the always-relevant core: every
        pinned tool, then native/skill tools (the ones that need no server), then
        MCP tools by name. Stability matters more than cleverness here — the same
        catalogue must produce the same connection payload every time.
        """
        entries = self.entries()
        tier: List[ToolEntry] = []
        for source in ("native", "skill"):
            tier.extend(e for e in entries if e.source == source and not e.pinned)
        tier.extend(e for e in entries if e.source.startswith("mcp:"))
        tier.extend(e for e in entries if not e.source.startswith("mcp:")
                    and e.source not in ("native", "skill"))
        return [SearchHit(entry=e, score=1.0) for e in tier[:room]]

    def render_skill_context(self, skill_ids: Sequence[str], max_chars: int = 4000) -> str:
        """Assemble the ``[ACTIVE SKILL CONTEXT]`` block.

        One manual per *group*, not per tool: 80 Synapse tools share one guide.
        That is the difference between a few hundred and a few tens of
        thousands of tokens of instructions.
        """
        blocks: List[str] = []
        used = 0
        for skill_id in skill_ids:
            manual = self.manual(skill_id)
            if manual is None:
                continue
            rendered = manual.rendered(max_chars=max(200, max_chars - used))
            if not rendered:
                continue
            blocks.append(rendered)
            used += len(rendered)
            if used >= max_chars:
                break
        if not blocks:
            return ""
        return (
            "[ACTIVE SKILL CONTEXT]\n"
            "Usage rules for the tool groups available this turn. They do not apply "
            "to any other capability.\n\n" + "\n\n".join(blocks)
        )

    # -- diagnostics ------------------------------------------------------- #

    def stats(self) -> IndexStats:
        with self._lock:
            self._stats.tools = sum(
                1 for n in self._entries if n not in self._disabled
            )
            self._stats.manuals = len(self._manuals)
            return self._stats

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)
            self._stats.enabled = self._enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_disabled(self, names: Iterable[str]) -> None:
        """Replaces the whole switched-off set. Cheap; the index rebuilds lazily."""
        with self._lock:
            self._disabled = {str(n) for n in (names or []) if str(n)}
            self._built = False

    def disable(self, name: str) -> None:
        with self._lock:
            if name and name not in self._disabled:
                self._disabled.add(name)
                self._built = False

    def enable(self, name: str) -> None:
        with self._lock:
            if name in self._disabled:
                self._disabled.discard(name)
                self._built = False

    def disabled(self) -> set:
        with self._lock:
            return set(self._disabled)

    def is_disabled(self, name: str) -> bool:
        with self._lock:
            return name in self._disabled

    def explain(self, query: str, k: int = 8) -> List[Dict[str, Any]]:
        """Diagnostic view: what each backend contributed to the ranking."""
        out: List[Dict[str, Any]] = []
        for hit in self.search(query, k=k):
            out.append(
                {
                    "name": hit.name,
                    "score": round(hit.score, 6),
                    "source": hit.entry.source,
                    "skill_id": hit.entry.skill_id,
                    "domain": hit.entry.domain,
                    "pinned": hit.entry.pinned,
                }
            )
        return out


def _estimate_tokens(entries: Sequence[ToolEntry]) -> int:
    """Rough token cost of a declaration set, for the settings UI readout.

    Deliberately crude (~4 chars/token). It only needs to answer "did this
    change get smaller", not to be a billing figure.
    """
    chars = 0
    for entry in entries:
        try:
            chars += len(json.dumps(entry.declaration, default=str))
        except (TypeError, ValueError):
            chars += len(entry.name) + len(entry.description)
    return chars // 4


# --------------------------------------------------------------------------- #
# Meta-tool declarations
# --------------------------------------------------------------------------- #

SEARCH_TOOLS_DECLARATION: Dict[str, Any] = {
    "name": META_SEARCH_TOOL,
    "description": (
        "Find the tools available for a task without loading every tool into context. "
        "Returns the exact name, purpose and argument schema of the best matches, plus "
        "any usage rules for that tool group. "
        "Call this FIRST whenever you need a capability that is not among your current "
        "tools — for example when the user asks for something in a domain you have not "
        "been given a tool for. Then call the returned tool with call_tool."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "intent": {
                "type": "STRING",
                "description": (
                    "What you want to do, in the user's own words "
                    "(e.g. 'control the brightness of the monitors', 'search my vault for notes')."
                ),
            },
            "limit": {
                "type": "INTEGER",
                "description": "How many tools to return. Default 6, maximum 15.",
            },
        },
        "required": ["intent"],
    },
}

CALL_TOOL_DECLARATION: Dict[str, Any] = {
    "name": META_CALL_TOOL,
    "description": (
        "Run a tool that you retrieved with search_tools but do not have loaded. "
        "Use the tool name exactly as search_tools returned it. "
        "Prefer a directly-loaded tool when one exists; use this for everything else."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "tool_name": {
                "type": "STRING",
                "description": "Exact tool name from search_tools, e.g. 'mcp_Synapse_target_act'.",
            },
            "arguments": {
                "type": "OBJECT",
                "description": "Arguments for the tool, matching the schema search_tools returned.",
            },
        },
        "required": ["tool_name", "arguments"],
    },
}

GET_MANUAL_DECLARATION: Dict[str, Any] = {
    "name": META_MANUAL_TOOL,
    "description": (
        "Read the full usage manual for a tool group (for example 'Synapse' or 'Calyx') "
        "when the short rules you already have are not enough — typically because a "
        "workflow has required ordering or constraints. Costs a turn, so read it only "
        "when you actually need the detail."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "skill_id": {
                "type": "STRING",
                "description": "Group id from a tool's skill_id field, e.g. 'Synapse' or 'mcp:Calyx'.",
            },
        },
        "required": ["skill_id"],
    },
}

META_DECLARATIONS: Tuple[Dict[str, Any], ...] = (
    SEARCH_TOOLS_DECLARATION,
    CALL_TOOL_DECLARATION,
    GET_MANUAL_DECLARATION,
)


# --------------------------------------------------------------------------- #
# Process-wide instance
# --------------------------------------------------------------------------- #

_index_lock = threading.Lock()
_index: Optional[ToolIndex] = None


def get_index() -> ToolIndex:
    """Returns the process-wide index, creating it on first use."""
    global _index
    with _index_lock:
        if _index is None:
            _index = ToolIndex()
        return _index


def reset_index() -> None:
    """Drops the process-wide instance. Test/CLI seam only."""
    global _index
    with _index_lock:
        _index = None