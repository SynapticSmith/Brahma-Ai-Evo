"""Automatic retrieval over the user's Obsidian second brain.

The point of this module is that a small local model does not have to know the
vault exists, remember a tool name, or decide to search. Before a turn reaches
the model, :func:`context_for_turn` retrieves the most relevant pages and the
loop injects them as one bounded block. Tools (``brain_search`` and friends)
exist only for deliberate depth; they are not the default path.

Design constraints
------------------
* **Stdlib at module scope.** ``sqlite3`` (FTS5), ``urllib``, ``hashlib`` and
  ``array``. Optional ``numpy`` is imported lazily to speed up the vector scan;
  the pure-Python path is the fallback. ``agent/`` must import with only pytest
  installed.
* **Never fatal.** A missing vault, a missing index or a dead embedding server
  degrades to keyword search, then to nothing. Retrieval must not be able to
  fail a turn.
* **Read-only over the vault.** The index lives in Brahma's own data directory.
  No vault page is ever written here.
* **Bounded.** Both the number of chunks and the rendered context have hard
  caps; a 1.7B model has an 8K window and a 2.4 KB block is already ~7% of it.
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 2
EMBED_DIM = 768
DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_EMBED_ENDPOINT = "http://localhost:11434/v1"
DEFAULT_VAULT = Path.home() / "Documents" / "Obsidian" / "My Vault"

#: Weights by page/chunk kind for retrieval ranking (§8.2).
KIND_WEIGHTS: Dict[str, float] = {
    "synthesis": 1.10,
    "concept": 1.00,
    "entity": 0.95,
    "book": 0.90,
    "reference": 0.70,
    "index": 0.55,
}

#: Directories that are not part of the searchable knowledge base. ``raw`` is
#: deliberately absent: it is the immutable source shelf and should be found.
EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".obsidian",
        ".trash",
        "node_modules",
        "ai-chats",
        "exports",
        "_archive",
        "__pycache__",
    }
)
EXCLUDED_PREFIXES = ("subagent-orchestration/agents/",)

CHUNK_CHARS = 2_400
CHUNK_OVERLAP = 240
MAX_PAGE_BYTES = 400_000
DEFAULT_CONTEXT_CHARS = 2_400
MAX_CONTEXT_CHARS = 6_000
EMBED_BATCH = 8
EMBED_TIMEOUT = 120.0
QUERY_TIMEOUT = 4.0
MAX_CHUNKS_PER_PAGE = 2
RRF_K = 60
#: Vector fusion is only trusted once this fraction of chunks is embedded.
#: Below it, keyword ranking is more reliable than a handful of stray vectors.
VECTOR_COVERAGE_MIN = 0.8

_LOCK = threading.RLock()
_VECTOR_CACHE: Dict[str, Any] = {"key": None, "rows": []}

#: Turns that are not knowledge questions. Retrieval on "hi" or "git status"
#: is pure noise, and the vault's own cheap-exit rule says the same thing.
_SKIP_EXACT = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "thanks",
        "thank you",
        "ok",
        "okay",
        "yes",
        "no",
        "continue",
        "go on",
        "stop",
        "never mind",
        "nevermind",
    }
)
_SKIP_PREFIX = re.compile(
    r"^(?:run|git|pip|pip3|npm|pnpm|yarn|cd|ls|dir|cls|type|where|which|"
    r"python|pytest|ruff|node|curl|winget|choco)\b",
    re.IGNORECASE,
)

#: Words that carry no retrieval signal. Kept deliberately small: the goal is
#: to stop "how do I fix the failing test" from matching every page containing
#: "test", not to build a linguistics pipeline.
_STOPWORDS = frozenset(
    """a an the and or but if then than that this these those is are was were be
    been being do does did doing have has had having i you he she it we they me
    him her us them my your his its our their of to in on at by for with about
    against between into through during before after above below from up down
    out off over under again further once here there when where why how all any
    both each few more most other some such no nor not only own same so too very
    can will just don should now what which who whom whose please tell give show
    explain""".split()
)


def _content_tokens(text: str) -> List[str]:
    """Lowercase words that could plausibly index a note."""
    out: List[str] = []
    for token in re.findall(r"[A-Za-z0-9_]{3,}", str(text or "")):
        lowered = token.lower()
        if lowered in _STOPWORDS or lowered in out:
            continue
        out.append(lowered)
    return out


def _token_in(token: str, haystack: str) -> bool:
    """Crude plural folding; enough to match "answers" against "answer"."""
    if token in haystack:
        return True
    stem = token[:-1] if len(token) > 4 and token.endswith("s") else token
    return len(stem) >= 4 and stem in haystack


def _is_relevant(query: str, item: Dict[str, Any]) -> bool:
    """Would this note actually help answer the query?

    Keyword-only retrieval has no calibrated score, so relevance is judged on
    term overlap: at least two content words (or the only one, for a short
    query) must appear in the note. A semantic hit is accepted on its cosine,
    because synonyms legitimately share no words.
    """
    cosine = item.get("cosine")
    if cosine is not None and cosine >= 0.5:
        return True
    tokens = _content_tokens(query)
    if not tokens:
        return False
    haystack = f"{item.get('heading') or ''} {item.get('text') or ''}".lower()
    hits = sum(1 for token in tokens if _token_in(token, haystack))
    if len(tokens) <= 2:
        # "weather in Paris" must match both words, not just "weather"; a
        # single-word query still works ("calyx").
        return hits == len(tokens)
    return hits >= 2 and hits / len(tokens) >= 0.25


# --------------------------------------------------------------------------- #
# Location and configuration
# --------------------------------------------------------------------------- #

def _data_dir() -> Path:
    """Brahma's per-user data directory, without importing the GUI layer."""
    try:
        from core.user_paths import get_user_data_dir

        base = get_user_data_dir()
    except Exception:  # pragma: no cover - non-repo / frozen fallback
        base = Path(os.getenv("LOCALAPPDATA", os.path.expanduser("~"))) / "BrahmaAI"
    target = Path(base) / "second_brain"
    target.mkdir(parents=True, exist_ok=True)
    return target


def index_path() -> Path:
    return _data_dir() / "index.db"


def config_path() -> Path:
    return _data_dir() / "config.json"


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def config() -> Dict[str, Any]:
    """Effective configuration: defaults < config file < environment."""
    data: Dict[str, Any] = {
        "enabled": True,
        "vault": str(DEFAULT_VAULT),
        "embed_endpoint": DEFAULT_EMBED_ENDPOINT,
        "embed_model": DEFAULT_EMBED_MODEL,
        "embed_prefixes": True,
        "context_chars": DEFAULT_CONTEXT_CHARS,
        "max_chunks_per_page": MAX_CHUNKS_PER_PAGE,
    }
    data.update(_read_json(config_path()))

    env_vault = os.getenv("BRAHMA_SECOND_BRAIN_VAULT", "").strip()
    if env_vault:
        data["vault"] = env_vault
    if os.getenv("BRAHMA_SECOND_BRAIN", "").strip() in ("0", "false", "off", "no"):
        data["enabled"] = False
    env_endpoint = os.getenv("BRAHMA_EMBED_ENDPOINT", "").strip()
    if env_endpoint:
        data["embed_endpoint"] = env_endpoint
    env_model = os.getenv("BRAHMA_EMBED_MODEL", "").strip()
    if env_model:
        data["embed_model"] = env_model
    try:
        data["context_chars"] = max(
            400, min(MAX_CONTEXT_CHARS, int(data.get("context_chars") or DEFAULT_CONTEXT_CHARS))
        )
    except (TypeError, ValueError):
        data["context_chars"] = DEFAULT_CONTEXT_CHARS
    return data


def vault_root() -> Optional[Path]:
    raw = str(config().get("vault") or "").strip()
    if not raw:
        return None
    root = Path(raw).expanduser()
    try:
        return root.resolve() if root.is_dir() else None
    except OSError:
        return None


def enabled() -> bool:
    """True when a vault exists and the user has not switched retrieval off."""
    return bool(config().get("enabled")) and vault_root() is not None


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #

def _connect() -> sqlite3.Connection:
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.DatabaseError:
        pass
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pages (
            path    TEXT PRIMARY KEY,
            mtime   REAL NOT NULL,
            size    INTEGER NOT NULL,
            sha     TEXT NOT NULL,
            title   TEXT,
            shelf   TEXT,
            kind    TEXT,
            domain  TEXT,
            quality REAL
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            path    TEXT NOT NULL,
            ord     INTEGER NOT NULL,
            heading TEXT,
            text    TEXT NOT NULL,
            kind    TEXT
        );
        CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text, heading, path UNINDEXED, tokenize='porter unicode61'
        );
        CREATE TABLE IF NOT EXISTS vectors (
            chunk_id INTEGER PRIMARY KEY,
            vec      BLOB NOT NULL,
            norm     REAL NOT NULL
        );
        """
    )
    try:
        cur_version = int(_get_meta(conn, "schema", 0) or 0)
    except (ValueError, TypeError):
        cur_version = 0

    if cur_version < SCHEMA_VERSION:
        try:
            pages_cols = {row["name"] for row in conn.execute("PRAGMA table_info(pages)")}
            if "kind" not in pages_cols:
                conn.execute("ALTER TABLE pages ADD COLUMN kind TEXT")
            if "domain" not in pages_cols:
                conn.execute("ALTER TABLE pages ADD COLUMN domain TEXT")
            if "quality" not in pages_cols:
                conn.execute("ALTER TABLE pages ADD COLUMN quality REAL")

            chunks_cols = {row["name"] for row in conn.execute("PRAGMA table_info(chunks)")}
            if "kind" not in chunks_cols:
                conn.execute("ALTER TABLE chunks ADD COLUMN kind TEXT")

            # Backfill any pages missing kind
            pages_to_backfill = list(
                conn.execute("SELECT path FROM pages WHERE kind IS NULL")
            )
            for row in pages_to_backfill:
                p = str(row["path"])
                k = _classify_kind(p)
                d = _extract_domain(p)
                conn.execute(
                    "UPDATE pages SET kind = ?, domain = ?, quality = 1.0 WHERE path = ?",
                    (k, d, p),
                )
            # Backfill chunks kind from pages
            conn.execute(
                "UPDATE chunks SET kind = (SELECT kind FROM pages WHERE pages.path = chunks.path) "
                "WHERE chunks.kind IS NULL"
            )
            _set_meta(conn, "schema", str(SCHEMA_VERSION))
            conn.commit()
        except Exception as exc:
            # Report schema_error in meta; retrieval must not break (§8.1)
            _set_meta(conn, "schema_error", str(exc))
            conn.commit()
    else:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()


def _set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (key, str(value)),
    )


def _get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else default


def index_exists() -> bool:
    try:
        return index_path().is_file() and index_path().stat().st_size > 0
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Vault walking and chunking
# --------------------------------------------------------------------------- #

def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _excluded(rel: str) -> bool:
    parts = rel.split("/")
    if any(part in EXCLUDED_PARTS for part in parts):
        return True
    return any(rel.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def iter_pages(root: Optional[Path] = None) -> Iterable[Path]:
    base = root or vault_root()
    if base is None:
        return []
    pages: List[Path] = []
    for path in base.rglob("*.md"):
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        rel = _relative(path, base)
        if _excluded(rel):
            continue
        pages.append(path)
    pages.sort(key=lambda p: _relative(p, base).lower())
    return pages


def _split_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """Return simple ``key: value`` frontmatter and the body after it."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: Dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return meta, "\n".join(lines[index + 1 :])
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip().strip("\"'")
    return {}, text


def _title_for(rel: str, body: str, meta: Dict[str, str]) -> str:
    title = str(meta.get("title") or "").strip()
    if title:
        return title
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return Path(rel).stem


def _classify_kind(rel_path: str, meta: Optional[Dict[str, Any]] = None) -> str:
    """Classify note kind into one of: index, reference, book, synthesis, entity, concept."""
    norm_path = rel_path.replace("\\", "/").lower()
    file_name = norm_path.split("/")[-1]

    # Explicit frontmatter type/kind has highest priority
    if meta:
        raw_type = str(meta.get("type") or meta.get("kind") or "").strip().lower()
        if raw_type:
            if raw_type in ("index", "reference", "book", "synthesis", "entity", "concept"):
                return raw_type
            if raw_type == "note":
                return "concept"
            if raw_type in ("booknote", "book_note", "book-note"):
                return "book"
            if raw_type in ("synthesisnote", "synthesis_note", "synthesis-note"):
                return "synthesis"
            if raw_type in ("entitynote", "entity_note", "entity-note"):
                return "entity"

    # Path heuristics
    if file_name == "index.md" or norm_path.endswith("/_index.md"):
        return "index"
    if file_name == "wikipedia.md" or norm_path.endswith("/_wikipedia.md"):
        return "reference"
    if norm_path.startswith("book-library/") or "/book-library/" in norm_path:
        return "book"
    if norm_path.startswith("wiki/synthesis/") or "/synthesis/" in norm_path:
        return "synthesis"
    if norm_path.startswith("wiki/entities/") or "/entities/" in norm_path:
        return "entity"
    if norm_path.startswith("_taxonomy/") or "/_taxonomy/" in norm_path:
        return "index"

    return "concept"


def _extract_domain(rel_path: str, meta: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Extract canonical academic taxonomy domain ID for a page."""
    if meta:
        domain = meta.get("domain")
        if domain and isinstance(domain, str) and domain.strip():
            return domain.strip()

    norm = rel_path.replace("\\", "/").strip("/")
    prefix = "wiki/library/knowledge/"
    if prefix in norm:
        idx = norm.find(prefix) + len(prefix)
        remainder = norm[idx:]
        parts = remainder.split("/")
        if len(parts) > 1:
            return "/".join(parts[:-1])
        elif len(parts) == 1 and parts[0].endswith(".md"):
            stem = parts[0][:-3]
            return stem if stem != "index" else None
    return None


def _extract_quality(meta: Optional[Dict[str, Any]] = None) -> float:
    """Extract quality score (0.0 to 1.0) from note metadata."""
    if not meta:
        return 1.0
    if "quality" in meta:
        try:
            return max(0.0, min(1.0, float(meta["quality"])))
        except (TypeError, ValueError):
            pass
    status = str(meta.get("status") or "").strip().lower()
    if status == "reviewed":
        return 1.0
    elif status == "draft":
        return 0.5
    elif status == "deprecated":
        return 0.2
    if meta.get("draft") is True or str(meta.get("draft", "")).lower() in ("true", "1", "yes"):
        return 0.5
    return 1.0


def _split_long(text: str) -> List[str]:
    if len(text) <= CHUNK_CHARS:
        return [text]
    out: List[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(length, start + CHUNK_CHARS)
        if end < length:
            # Prefer a paragraph or sentence boundary near the cap.
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(". "))
            if cut >= CHUNK_CHARS // 2:
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= length:
            break
        start = max(start + 1, end - CHUNK_OVERLAP)
    return out


def chunk_text(body: str) -> List[Tuple[str, str]]:
    """Split a page into ``(heading, text)`` chunks of roughly ``CHUNK_CHARS``.

    Sections are *packed*, not emitted one per heading: a page with a heading
    every few lines would otherwise become hundreds of tiny chunks, which both
    bloats the index and gives retrieval fragments too small to answer from.
    The heading line stays in the chunk text so the model still sees structure;
    the returned heading is the first section's label.
    """
    sections: List[Tuple[str, str]] = []
    heading = ""
    buffer: List[str] = []

    def flush() -> None:
        nonlocal buffer
        text = "\n".join(buffer).strip()
        buffer = []
        if text or heading:
            sections.append((heading, text))

    for line in body.splitlines():
        match = re.match(r"^#{1,6}\s+(.*)$", line)
        if match:
            flush()
            heading = match.group(1).strip()
            buffer = [line]
            continue
        buffer.append(line)
    flush()

    packed: List[Tuple[str, str]] = []
    current_heading = ""
    current: List[str] = []
    current_len = 0
    for section_heading, text in sections:
        if not text:
            continue
        if current and current_len + len(text) + 2 > CHUNK_CHARS:
            packed.append((current_heading, "\n\n".join(current)))
            current = []
            current_len = 0
        if not current:
            current_heading = section_heading
        current.append(text)
        current_len += len(text) + 2
    if current:
        packed.append((current_heading, "\n\n".join(current)))

    chunks: List[Tuple[str, str]] = []
    for chunk_heading, text in packed:
        for piece in _split_long(text):
            chunks.append((chunk_heading, piece))
    return chunks


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


# --------------------------------------------------------------------------- #
# Embeddings
# --------------------------------------------------------------------------- #

def _embed(texts: Sequence[str], *, endpoint: str, model: str, prefixes: bool,
           timeout: float = EMBED_TIMEOUT) -> List[List[float]]:
    payload: Dict[str, Any] = {"model": model, "input": list(texts)}
    request = urllib.request.Request(
        f"{endpoint.rstrip('/')}/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise ValueError("embedding endpoint returned no data list")
    data = sorted(data, key=lambda item: item.get("index", 0))
    vectors = [list(map(float, item["embedding"])) for item in data]
    if len(vectors) != len(texts):
        raise ValueError(
            f"embedding endpoint returned {len(vectors)} vectors for {len(texts)} inputs"
        )
    return vectors


def _embed_documents(texts: Sequence[str], *, prefixes: bool = True) -> List[List[float]]:
    cfg = config()
    prepared = [f"search_document: {t}" if prefixes else t for t in texts]
    return _embed(
        prepared,
        endpoint=str(cfg.get("embed_endpoint") or DEFAULT_EMBED_ENDPOINT),
        model=str(cfg.get("embed_model") or DEFAULT_EMBED_MODEL),
        prefixes=prefixes,
    )


def _embed_query(text: str) -> Optional[List[float]]:
    cfg = config()
    prefixes = bool(cfg.get("embed_prefixes", True))
    prepared = f"search_query: {text}" if prefixes else text
    try:
        vectors = _embed(
            [prepared],
            endpoint=str(cfg.get("embed_endpoint") or DEFAULT_EMBED_ENDPOINT),
            model=str(cfg.get("embed_model") or DEFAULT_EMBED_MODEL),
            prefixes=prefixes,
            timeout=QUERY_TIMEOUT,
        )
    except Exception:
        return None
    return vectors[0] if vectors else None


def _pack(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack(blob: bytes) -> array.array:
    values = array.array("f")
    values.frombytes(blob)
    return values


def _norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(value * value for value in vector)) or 1.0


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #

def sync(
    *,
    rebuild: bool = False,
    embed: bool = True,
    embed_index: bool = False,
    limit: Optional[int] = None,
    embed_limit: Optional[int] = None,
    progress: Optional[Any] = None,
    shelf: Optional[str] = None,
) -> Dict[str, Any]:
    """Incrementally index the vault. Safe to run while a query is open.

    ``progress`` is called as ``progress(done, total, rel_path)`` and may be
    ``None``. A failed embedding server does not abort the sync: the chunks are
    still searchable by keyword, and ``meta.embed_error`` records why.

    ``embed_limit`` caps how many chunks get embeddings in one run. A CPU-only
    ``nomic-embed-text`` needs ~1.7 s per chunk, so a full 8k-chunk pass is
    hours; bounded runs make progress without pinning the machine.
    ``shelf`` restricts indexing and embedding to a specific subfolder (e.g. 'wiki/').
    ``embed_index`` toggles embedding of index and reference pages (default False).
    """
    root = vault_root()
    if root is None:
        return {
            "ok": False,
            "error": f"vault not found: {config().get('vault')}",
        }

    pages = list(iter_pages(root))
    if shelf:
        clean_shelf = shelf.replace("\\", "/").strip("/") + "/"
        pages = [p for p in pages if _relative(p, root).startswith(clean_shelf) or _relative(p, root) == shelf.strip("/")]
    if limit is not None:
        pages = pages[: max(0, int(limit))]

    cfg = config()
    prefixes = bool(cfg.get("embed_prefixes", True))
    stats = {
        "ok": True,
        "vault": str(root),
        "pages_seen": len(pages),
        "pages_indexed": 0,
        "pages_skipped": 0,
        "chunks": 0,
        "vectors": 0,
        "embed_errors": 0,
        "duration_s": 0.0,
    }
    import time as _time

    started = _time.time()
    embed_error = ""
    conn = _connect()
    try:
        _ensure_schema(conn)
        if rebuild:
            conn.execute("DELETE FROM vectors")
            conn.execute("DELETE FROM chunks_fts")
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM pages")
            conn.commit()

        known = {
            row["path"]: row["sha"]
            for row in conn.execute("SELECT path, sha FROM pages")
        }

        for number, path in enumerate(pages, start=1):
            rel = _relative(path, root)
            try:
                stat = path.stat()
                raw = path.read_bytes()[:MAX_PAGE_BYTES]
            except OSError:
                continue
            text = raw.decode("utf-8", "replace")
            sha = _sha(text)
            if known.get(rel) == sha:
                stats["pages_skipped"] += 1
                if progress:
                    progress(number, len(pages), rel)
                continue

            meta, body = _split_frontmatter(text)
            title = _title_for(rel, body, meta)
            page_shelf = rel.split("/")[0] if "/" in rel else "(root)"
            kind = _classify_kind(rel, meta)
            domain = _extract_domain(rel, meta)
            quality = _extract_quality(meta)
            pieces = chunk_text(body)

            old_ids = [
                row["id"]
                for row in conn.execute("SELECT id FROM chunks WHERE path = ?", (rel,))
            ]
            if old_ids:
                conn.executemany(
                    "DELETE FROM chunks_fts WHERE rowid = ?", [(i,) for i in old_ids]
                )
                conn.executemany(
                    "DELETE FROM vectors WHERE chunk_id = ?", [(i,) for i in old_ids]
                )
                conn.execute("DELETE FROM chunks WHERE path = ?", (rel,))

            inserted: List[Tuple[int, str]] = []
            for order, (heading, piece) in enumerate(pieces):
                cursor = conn.execute(
                    "INSERT INTO chunks(path, ord, heading, text, kind) VALUES (?, ?, ?, ?, ?)",
                    (rel, order, heading, piece, kind),
                )
                chunk_id = int(cursor.lastrowid)
                conn.execute(
                    "INSERT INTO chunks_fts(rowid, text, heading, path) VALUES (?, ?, ?, ?)",
                    (chunk_id, piece, heading, rel),
                )
                inserted.append((chunk_id, piece))

            conn.execute(
                "INSERT OR REPLACE INTO pages(path, mtime, size, sha, title, shelf, kind, domain, quality) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rel, stat.st_mtime, stat.st_size, sha, title, page_shelf, kind, domain, quality),
            )
            stats["pages_indexed"] += 1
            stats["chunks"] += len(inserted)
            conn.commit()

            if (
                embed
                and inserted
                and not embed_error
                and embed_limit is None
                and (embed_index or kind not in ("index", "reference"))
            ):
                for start in range(0, len(inserted), EMBED_BATCH):
                    batch = inserted[start : start + EMBED_BATCH]
                    try:
                        vectors = _embed_documents(
                            [piece for _, piece in batch], prefixes=prefixes
                        )
                    except Exception as exc:  # noqa: BLE001 - degrade, never abort
                        embed_error = f"{type(exc).__name__}: {exc}"
                        stats["embed_errors"] += 1
                        break
                    conn.executemany(
                        "INSERT OR REPLACE INTO vectors(chunk_id, vec, norm) "
                        "VALUES (?, ?, ?)",
                        [
                            (chunk_id, _pack(vector), _norm(vector))
                            for (chunk_id, _), vector in zip(batch, vectors)
                        ],
                    )
                    stats["vectors"] += len(batch)
                conn.commit()

            if progress:
                progress(number, len(pages), rel)

        # Backfill: a keyword-only build (``--no-embed``) or a previous
        # embedding failure leaves chunks without vectors. Filling them here
        # means a normal run completes the index instead of requiring a rebuild.
        if embed and not embed_error:
            remaining = embed_limit
            while remaining is None or remaining > 0:
                take = EMBED_BATCH if remaining is None else min(EMBED_BATCH, remaining)
                where_clauses: List[str] = ["v.chunk_id IS NULL"]
                query_params: List[Any] = []
                if shelf:
                    norm_shelf = shelf.replace("\\", "/").strip("/")
                    where_clauses.append("c.path LIKE ?")
                    query_params.append(f"{norm_shelf}%")
                if not embed_index:
                    where_clauses.append("(c.kind IS NULL OR c.kind NOT IN ('index', 'reference'))")
                query_params.append(take)
                where_sql = " AND ".join(where_clauses)
                missing = list(
                    conn.execute(
                        f"SELECT c.id, c.text FROM chunks c "
                        f"LEFT JOIN vectors v ON v.chunk_id = c.id "
                        f"WHERE {where_sql} LIMIT ?",
                        tuple(query_params),
                    )
                )
                if not missing:
                    break
                try:
                    vectors = _embed_documents(
                        [row["text"] for row in missing], prefixes=prefixes
                    )
                except Exception as exc:  # noqa: BLE001 - degrade, never abort
                    embed_error = f"{type(exc).__name__}: {exc}"
                    stats["embed_errors"] += 1
                    break
                conn.executemany(
                    "INSERT OR REPLACE INTO vectors(chunk_id, vec, norm) "
                    "VALUES (?, ?, ?)",
                    [
                        (int(row["id"]), _pack(vector), _norm(vector))
                        for row, vector in zip(missing, vectors)
                    ],
                )
                stats["vectors"] += len(missing)
                conn.commit()
                if remaining is not None:
                    remaining -= len(missing)

        _set_meta(conn, "last_sync", _time.time())
        _set_meta(conn, "vault", str(root))
        _set_meta(conn, "embed_model", cfg.get("embed_model"))
        _set_meta(conn, "embed_dim", EMBED_DIM)
        _set_meta(conn, "embed_error", embed_error)
        conn.commit()
    finally:
        conn.close()

    stats["duration_s"] = round(_time.time() - started, 1)
    stats["embed_error"] = embed_error
    clear_cache()
    return stats


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #

def _fts_query(text: str) -> Optional[str]:
    tokens = [token for token in re.findall(r"[A-Za-z0-9_]{2,}", text) if len(token) > 1]
    if not tokens:
        return None
    # A quoted OR keeps FTS5 from interpreting punctuation as syntax. Cap the
    # term count: a pasted paragraph should not become a 300-term query.
    unique: List[str] = []
    for token in tokens:
        if token.lower() not in {t.lower() for t in unique}:
            unique.append(token)
    return " OR ".join(f'"{token}"' for token in unique[:16])


def _cached_vectors(conn: sqlite3.Connection) -> List[Tuple[int, array.array, float]]:
    try:
        stat = index_path().stat()
        key = (str(index_path()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = None
    with _LOCK:
        if _VECTOR_CACHE["key"] == key:
            return _VECTOR_CACHE["rows"]
    rows: List[Tuple[int, array.array, float]] = []
    try:
        for row in conn.execute("SELECT chunk_id, vec, norm FROM vectors"):
            rows.append((int(row["chunk_id"]), _unpack(row["vec"]), float(row["norm"])))
    except sqlite3.DatabaseError:
        rows = []
    with _LOCK:
        _VECTOR_CACHE["key"] = key
        _VECTOR_CACHE["rows"] = rows
    return rows


def _cosine_scores(
    query: Sequence[float], rows: Sequence[Tuple[int, array.array, float]]
) -> Dict[int, float]:
    if not rows:
        return {}
    query_norm = _norm(query)
    try:
        import numpy as np  # type: ignore

        matrix = np.array([row[1].tolist() for row in rows], dtype="float32")
        q = np.array(query, dtype="float32")
        norms = np.array([row[2] for row in rows], dtype="float32")
        sims = (matrix @ q) / (norms * query_norm)
        return {rows[i][0]: float(sims[i]) for i in range(len(rows))}
    except Exception:
        pass
    out: Dict[int, float] = {}
    for chunk_id, vector, norm in rows:
        if len(vector) != len(query):
            continue
        total = 0.0
        for left, right in zip(vector, query):
            total += left * right
        out[chunk_id] = total / (norm * query_norm)
    return out


def _vector_coverage(conn: sqlite3.Connection, *, include_index: bool = False) -> float:
    """Fraction of chunks that have an embedding (0.0 when unknown).

    By default evaluates priority kinds (concept, book, synthesis, entity) per §8.3.
    """
    try:
        if include_index:
            chunks = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
            vectors = int(conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0])
        else:
            chunks = int(
                conn.execute(
                    "SELECT COUNT(*) FROM chunks WHERE kind IS NULL OR kind NOT IN ('index', 'reference')"
                ).fetchone()[0]
            )
            vectors = int(
                conn.execute(
                    "SELECT COUNT(*) FROM vectors v JOIN chunks c ON c.id = v.chunk_id "
                    "WHERE c.kind IS NULL OR c.kind NOT IN ('index', 'reference')"
                ).fetchone()[0]
            )
    except sqlite3.DatabaseError:
        return 0.0
    if chunks <= 0:
        return 0.0
    return min(1.0, vectors / chunks)


def _snippet(text: str, query: str, cap: int = 520) -> str:
    clean = " ".join(text.split())
    if len(clean) <= cap:
        return clean
    tokens = [t for t in re.findall(r"[A-Za-z0-9_]{3,}", query)]
    lowered = clean.lower()
    best = -1
    for token in tokens:
        position = lowered.find(token.lower())
        if position >= 0 and (best < 0 or position < best):
            best = position
    if best < 0:
        return clean[:cap].rstrip() + "…"
    start = max(0, best - cap // 3)
    end = min(len(clean), start + cap)
    prefix = "…" if start else ""
    suffix = "…" if end < len(clean) else ""
    return f"{prefix}{clean[start:end].rstrip()}{suffix}"


def search(
    query: str,
    k: int = 6,
    *,
    shelf: Optional[str] = None,
    max_chunks_per_page: int = MAX_CHUNKS_PER_PAGE,
) -> List[Dict[str, Any]]:
    """Hybrid keyword + embedding search over the index.

    Returns ``[{path, heading, text, snippet, score, fts_rank, cosine}]``.
    Keyword and vector results are fused with reciprocal rank fusion, which is
    scale-free and does not need either signal to be calibrated.
    """
    text = " ".join(str(query or "").split())
    if not text or not index_exists():
        return []
    k = max(1, min(20, int(k)))

    conn = _connect()
    try:
        _ensure_schema(conn)
        fts_rows: List[sqlite3.Row] = []
        match = _fts_query(text)
        if match:
            try:
                fts_rows = list(
                    conn.execute(
                        "SELECT c.id, c.path, c.heading, c.text, "
                        "bm25(chunks_fts, 1.0, 3.0) AS rank "
                        "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
                        "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT 80",
                        (match,),
                    )
                )
            except sqlite3.DatabaseError:
                fts_rows = []

        fts_rank = {int(row["id"]): position for position, row in enumerate(fts_rows)}

        # A partially embedded index is worse than a keyword-only one: the few
        # vectors that exist can outrank genuinely relevant keyword hits purely
        # because they happened to be in the first embedded batch. Until most
        # chunks have a vector, skip the query embedding entirely (also saves a
        # network round-trip on every turn).
        coverage = _vector_coverage(conn)
        query_vector = _embed_query(text) if coverage >= VECTOR_COVERAGE_MIN else None
        cosine: Dict[int, float] = {}
        if query_vector is not None:
            cosine = _cosine_scores(query_vector, _cached_vectors(conn))
        vector_rank = {
            chunk_id: position
            for position, (chunk_id, _) in enumerate(
                sorted(cosine.items(), key=lambda item: item[1], reverse=True)[:80]
            )
        }

        ids = set(fts_rank) | set(vector_rank)
        if not ids:
            return []

        placeholders = ",".join("?" for _ in ids)
        rows = {
            int(row["id"]): row
            for row in conn.execute(
                f"SELECT c.id, c.path, c.heading, c.text, c.kind, p.domain, p.quality, p.title "
                f"FROM chunks c LEFT JOIN pages p ON p.path = c.path "
                f"WHERE c.id IN ({placeholders})",
                tuple(ids),
            )
        }
    finally:
        conn.close()

    scored: List[Dict[str, Any]] = []
    for chunk_id in ids:
        row = rows.get(chunk_id)
        if row is None:
            continue
        path = str(row["path"])
        if shelf and not (path == shelf or path.startswith(f"{shelf}/")):
            continue
        score = 0.0
        if chunk_id in fts_rank:
            score += 1.0 / (RRF_K + fts_rank[chunk_id])
        if chunk_id in vector_rank:
            score += 1.0 / (RRF_K + vector_rank[chunk_id])

        # Type-weighted ranking and quality discount (§8.2)
        kind = str(row["kind"] or _classify_kind(path))
        kind_weight = KIND_WEIGHTS.get(kind, 1.0)
        quality = float(row["quality"] or 1.0) if row["quality"] is not None else 1.0
        final_score = score * kind_weight * (quality if quality < 1.0 else 1.0)
        if quality <= 0.5:
            final_score *= 0.75

        # Title boost
        title = str(row["title"] or "")
        if title:
            t_lower = title.lower()
            q_lower = text.lower()
            if q_lower in t_lower or t_lower in q_lower:
                final_score += 0.015
            else:
                q_toks = set(_content_tokens(text))
                t_toks = set(_content_tokens(title))
                if q_toks and t_toks and (q_toks & t_toks):
                    final_score += 0.010 * (len(q_toks & t_toks) / len(q_toks))

        # Domain bonus
        domain = str(row["domain"] or "")
        if domain:
            domain_parts = [p.replace("-", " ") for p in domain.split("/")]
            q_lower = text.lower()
            if any(part in q_lower for part in domain_parts if len(part) > 2):
                final_score += 0.008

        scored.append(
            {
                "path": path,
                "heading": str(row["heading"] or ""),
                "text": str(row["text"]),
                "snippet": _snippet(str(row["text"]), text),
                "score": final_score,
                "kind": kind,
                "domain": domain,
                "fts_rank": fts_rank.get(chunk_id),
                "cosine": cosine.get(chunk_id),
            }
        )

    scored.sort(key=lambda item: item["score"], reverse=True)
    per_page: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    index_count = 0
    for item in scored:
        is_index = (item.get("kind") == "index") or item["path"].endswith("index.md")
        # Enforce max 2 index pages rule (§8.2)
        if is_index and index_count >= 2:
            continue
        count = per_page.get(item["path"], 0)
        if count >= max_chunks_per_page:
            continue
        per_page[item["path"]] = count + 1
        if is_index:
            index_count += 1
        out.append(item)
        if len(out) >= k:
            break
    return out


def context_for_turn(text: str, *, budget: Optional[int] = None) -> str:
    """The block the loop injects before the user's turn ("" when not useful)."""
    if not enabled() or not should_retrieve(text):
        return ""
    try:
        results = search(text, k=10)
    except Exception:
        return ""
    # Drop weak matches before rendering. On a small model a block of
    # tangential notes is worse than no block: it invites confident nonsense.
    results = [item for item in results if _is_relevant(text, item)][:8]
    if not results:
        return ""

    limit = budget or int(config().get("context_chars") or DEFAULT_CONTEXT_CHARS)
    lines = [
        "SECOND BRAIN — notes retrieved for this turn "
        "(from the user's curated Obsidian vault; cite the path when you use them):"
    ]
    used = len(lines[0])
    for number, item in enumerate(results, start=1):
        heading = f" › {item['heading']}" if item.get("heading") else ""
        header = f"[{number}] {item['path']}{heading}"
        body = item.get("snippet") or ""
        block = f"{header}\n{body}"
        if used + len(block) + 2 > limit:
            remaining = limit - used - len(header) - 2
            if remaining < 160:
                break
            block = f"{header}\n{body[:remaining].rstrip()}…"
        lines.append(block)
        used += len(block) + 2
        if used >= limit:
            break

    # Dual-Engine: Attach Calyx cryptographic provenance proofs if Calyx is
    # available and has matching constellations for this turn. ``fast_search``
    # bounds the cost: a hung Calyx costs one turn, then is skipped for a
    # cooldown instead of stalling every turn.
    try:
        from . import calyx_bridge

        hits = calyx_bridge.fast_search(text, k=3)
        if hits:
            calyx_lines = ["\nCALYX GROUNDED PROVENANCE (tamper-evident hash-chained ledger):"]
            for h in hits:
                cx = h.get("cx_id", "")[:12]
                prov = h.get("provenance") or {}
                seq = prov.get("ledger_seq", "")
                chash = (prov.get("chain_hash") or "")[:16]
                calyx_lines.append(
                    f"• [constellation {cx}…] ledger_seq={seq} chain_hash={chash}… (anchored fact)"
                )
            calyx_block = "\n".join(calyx_lines)
            if used + len(calyx_block) + 2 <= limit + 500:
                lines.append(calyx_block)
    except Exception:
        pass

    if len(lines) == 1:
        return ""
    lines.append("(If none of these notes bear on the request, ignore them.)")
    return "\n".join(lines)


def should_retrieve(text: str) -> bool:
    """Cheap gate: is this a knowledge question at all?"""
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered in _SKIP_EXACT:
        return False
    if lowered.startswith(("/", "!")):
        return False
    if _SKIP_PREFIX.match(lowered):
        return False
    # A turn with no content word ("hi", "ok then") has nothing to retrieve.
    return bool(_content_tokens(cleaned))


def retrieval_sources(block: str) -> List[str]:
    """The note paths named in a rendered SECOND BRAIN block, in order."""
    sources: List[str] = []
    for line in str(block or "").splitlines():
        if not line.startswith("["):
            continue
        try:
            path = line.split("] ", 1)[1].split(" ›", 1)[0]
        except IndexError:
            continue
        if path and path not in sources:
            sources.append(path)
    return sources


def retrieval_metadata(block: str) -> Dict[str, Any]:
    """Parse a rendered SECOND BRAIN block to extract sources, kind counts, and domains (§8.6)."""
    sources = retrieval_sources(block)
    if not sources or not index_exists():
        return {"sources": sources, "kinds": {}, "domains": []}
    kinds: Dict[str, int] = {}
    domains: List[str] = []
    try:
        conn = _connect()
        try:
            placeholders = ",".join("?" for _ in sources)
            rows = conn.execute(
                f"SELECT path, kind, domain FROM pages WHERE path IN ({placeholders})",
                tuple(sources),
            ).fetchall()
            for row in rows:
                k = str(row["kind"] or "concept")
                kinds[k] = kinds.get(k, 0) + 1
                d = row["domain"]
                if d and d not in domains:
                    domains.append(str(d))
        finally:
            conn.close()
    except Exception:
        pass
    return {"sources": sources, "kinds": kinds, "domains": domains}


def with_retrieval(
    messages: Sequence[Dict[str, Any]],
    text: str,
    *,
    budget: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """Return a copy of ``messages`` with this turn's notes before the last user.

    The GUI text path makes its first model call before ``AgentLoop`` exists.
    This helper lets that call see the same block without mutating the seed
    handed to the session bridge: the block is request context, not transcript.
    """
    block = context_for_turn(text, budget=budget)
    copied = [dict(message) for message in messages]
    if not block:
        return copied, ""
    at = len(copied)
    for index in range(len(copied) - 1, -1, -1):
        if copied[index].get("role") == "user":
            at = index
            break
    copied.insert(at, {"role": "system", "content": block})
    return copied, block


# --------------------------------------------------------------------------- #
# Prompt block, page reading, status
# --------------------------------------------------------------------------- #

def brain_map(*, automatic: bool = True) -> str:
    """A compact resident description of the vault for the system prompt.

    ``automatic`` is False on the voice surface: the Live audio socket has no
    pre-turn text hook, so there the model must call ``brain_search`` itself.
    """
    if not bool(config().get("enabled")):
        return ""
    root = vault_root()
    if root is None:
        return ""
    indexed = index_exists()
    if indexed and automatic:
        state = (
            "Notes are retrieved automatically for each request and appear as a "
            "SECOND BRAIN block."
        )
    elif indexed:
        state = "Call brain_search to look notes up; the index is built."
    else:
        state = "The index is not built yet; call brain_sync once to enable retrieval."
    return (
        "SECOND BRAIN (the user's curated Obsidian vault)\n"
        f"Root: {root}\n"
        "Shelves: wiki/ (synthesised knowledge and entity pages), raw/ (immutable sources), "
        "Personal notes/, book-library/, Calyx/ (grounding engine docs), operations/ (tooling). "
        "About 6,600 notes; subagent-orchestration/agents/, ai-chats/, exports/ and _archive/ "
        "are excluded from search.\n"
        f"{state} Prefer these notes over general knowledge, and cite the note path you used. "
        "If the block does not answer the question, call brain_search; use brain_read for a "
        "full page and brain_status to check freshness. Do not crawl the vault for generic "
        "software, syntax, or git questions."
    )


def read_page(page: str, max_chars: int = 8_000) -> str:
    """Read one vault page, contained to the vault root by construction."""
    root = vault_root()
    if root is None:
        raise ValueError("second-brain vault is not configured or does not exist")
    raw = str(page or "").strip().replace("\\", "/")
    if not raw:
        raise ValueError("page is required")
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("page must stay inside the second-brain vault") from exc
    if candidate.suffix.lower() != ".md":
        raise ValueError("only Markdown pages can be read")
    if not candidate.is_file():
        raise FileNotFoundError(f"no such page: {raw}")
    text = candidate.read_text(encoding="utf-8", errors="replace")
    cap = max(400, min(40_000, int(max_chars)))
    if len(text) > cap:
        text = text[:cap].rstrip() + "\n… (truncated)"
    return f"# {_relative(candidate, root)}\n\n{text}"


def status() -> Dict[str, Any]:
    root = vault_root()
    info: Dict[str, Any] = {
        "enabled": bool(config().get("enabled")),
        "vault": str(root) if root else None,
        "vault_configured": str(config().get("vault") or ""),
        "index": str(index_path()),
        "index_exists": index_exists(),
        "schema_version": 1,
        "schema_error": "",
        "embed_model": config().get("embed_model"),
        "pages": 0,
        "pages_by_kind": {},
        "chunks": 0,
        "chunks_by_kind": {},
        "vectors": 0,
        "vectors_missing": 0,
        "vectors_missing_by_kind": {},
        "vector_coverage": 0.0,
        "semantic_enabled": False,
        "last_sync": None,
        "embed_error": "",
    }
    if not index_exists():
        return info
    try:
        conn = _connect()
        try:
            _ensure_schema(conn)
            info["schema_version"] = int(_get_meta(conn, "schema", 1) or 1)
            info["schema_error"] = str(_get_meta(conn, "schema_error", "") or "")
            info["pages"] = int(conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0])
            info["chunks"] = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
            info["vectors"] = int(conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0])
            info["vectors_missing"] = max(0, info["chunks"] - info["vectors"])
            info["vector_coverage"] = (
                round(_vector_coverage(conn), 3) if info["chunks"] else 0.0
            )
            info["semantic_enabled"] = info["vector_coverage"] >= VECTOR_COVERAGE_MIN
            info["last_sync"] = _get_meta(conn, "last_sync")
            info["embed_error"] = _get_meta(conn, "embed_error", "") or ""

            # Backlog and breakdowns by kind (§8.3)
            pages_by_kind: Dict[str, int] = {}
            for row in conn.execute("SELECT kind, COUNT(*) AS cnt FROM pages GROUP BY kind"):
                pages_by_kind[str(row["kind"] or "unknown")] = int(row["cnt"])
            info["pages_by_kind"] = pages_by_kind

            chunks_by_kind: Dict[str, int] = {}
            for row in conn.execute("SELECT kind, COUNT(*) AS cnt FROM chunks GROUP BY kind"):
                chunks_by_kind[str(row["kind"] or "unknown")] = int(row["cnt"])
            info["chunks_by_kind"] = chunks_by_kind

            missing_by_kind: Dict[str, int] = {}
            for row in conn.execute(
                "SELECT c.kind, COUNT(*) AS cnt FROM chunks c "
                "LEFT JOIN vectors v ON v.chunk_id = c.id "
                "WHERE v.chunk_id IS NULL GROUP BY c.kind"
            ):
                missing_by_kind[str(row["kind"] or "unknown")] = int(row["cnt"])
            info["vectors_missing_by_kind"] = missing_by_kind
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        info["error"] = str(exc)
    return info


# --------------------------------------------------------------------------- #
# Academic taxonomy navigation (§8.8)
# --------------------------------------------------------------------------- #

_TAXONOMY_CACHE: Dict[str, Any] = {"data": None, "nodes_by_id": {}}


def _load_taxonomy() -> List[Dict[str, Any]]:
    with _LOCK:
        if _TAXONOMY_CACHE["data"] is not None:
            return _TAXONOMY_CACHE["data"]

    candidates: List[Path] = []
    try:
        repo_root = Path(__file__).resolve().parent.parent
        candidates.append(repo_root / "config" / "taxonomy.json")
    except Exception:
        pass
    root = vault_root()
    if root is not None:
        candidates.append(root / "wiki" / "library" / "knowledge" / "_taxonomy" / "taxonomy.json")
        candidates.append(root / "_taxonomy" / "taxonomy.json")

    data: List[Dict[str, Any]] = []
    for cand in candidates:
        if cand.is_file():
            try:
                raw = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    data = raw
                    break
            except Exception:
                continue

    nodes_by_id = {node["id"]: node for node in data if isinstance(node, dict) and "id" in node}
    with _LOCK:
        _TAXONOMY_CACHE["data"] = data
        _TAXONOMY_CACHE["nodes_by_id"] = nodes_by_id
    return data


def locate_nodes(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Locate candidate academic knowledge nodes matching a free-text topic or query (§8.8.2)."""
    text = " ".join(str(query or "").strip().lower().split())
    if not text:
        return []
    limit = max(1, min(10, int(limit)))
    taxonomy = _load_taxonomy()
    if not taxonomy:
        return []

    tokens = [t for t in re.findall(r"[a-z0-9_]{2,}", text) if t not in _STOPWORDS]
    candidates: List[Dict[str, Any]] = []

    for node in taxonomy:
        node_id = str(node.get("id") or "").lower()
        title = str(node.get("title") or "").lower()
        aliases = [str(a).lower() for a in node.get("aliases") or []]
        legacy = [str(l).lower() for l in node.get("legacy_domains") or []]
        scope = str(node.get("scope") or "").lower()

        slug = node_id.split("/")[-1]
        confidence = 0.0
        match_type = "none"

        # 1. Exact slug or ID match
        if text == node_id or text == slug:
            confidence = 1.0
            match_type = "exact_slug"
        elif text == title:
            confidence = 0.98
            match_type = "exact_title"
        # 2. Alias or legacy domain match
        elif any(text == a for a in aliases):
            confidence = 0.92
            match_type = "alias"
        elif any(text == l or text in l for l in legacy):
            confidence = 0.88
            match_type = "legacy_domain"
        elif any(a in text or text in a for a in aliases if len(a) > 3):
            confidence = 0.85
            match_type = "alias_substring"
        elif slug in text or title in text:
            confidence = 0.82
            match_type = "title_contained"
        # 3. Token match across title, slug, and scope
        elif tokens:
            haystack = f"{node_id} {title} {' '.join(aliases)} {scope}"
            hits = sum(1 for t in tokens if t in haystack)
            if hits == len(tokens):
                confidence = 0.75 + min(0.15, hits * 0.03)
                match_type = "keyword_full"
            elif hits >= 2 or (len(tokens) == 1 and hits == 1):
                ratio = hits / len(tokens)
                if ratio >= 0.5:
                    confidence = 0.50 + 0.25 * ratio
                    match_type = "keyword_partial"

        if confidence >= 0.40:
            candidates.append(
                {
                    "path": node.get("id"),
                    "title": node.get("title"),
                    "division": node_id.split("/")[0],
                    "confidence": round(confidence, 2),
                    "match_type": match_type,
                    "summary": (node.get("scope") or "")[:240],
                }
            )

    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return candidates[:limit]


def browse_node(path: str = "") -> Dict[str, Any]:
    """Browse the academic knowledge tree at a node path (§8.8.1)."""
    raw_path = str(path or "").strip().replace("\\", "/").strip("/")
    _load_taxonomy()
    nodes_by_id = _TAXONOMY_CACHE.get("nodes_by_id", {})

    canonical_divisions = [
        "humanities",
        "social-sciences",
        "natural-sciences",
        "formal-sciences",
        "applied-sciences",
    ]

    # Root browse
    if not raw_path or raw_path.lower() in ("root", "divisions", "/"):
        children = []
        for div_slug in canonical_divisions:
            div_node = nodes_by_id.get(div_slug)
            title = div_node.get("title") if div_node else div_slug.replace("-", " ").title()
            children.append({"slug": div_slug, "title": title, "path": div_slug})
        return {
            "path": "",
            "title": "Academic Knowledge Tree Root",
            "division": "root",
            "parent": None,
            "summary": "The 5 canonical divisions of the academic knowledge tree.",
            "children": children,
            "concepts": [],
            "listed_under": [],
            "has_wikipedia_article": False,
        }

    # Validate node existence
    node = nodes_by_id.get(raw_path)
    if not node:
        match = None
        suggestions: List[str] = []
        for nid in nodes_by_id:
            if nid.lower() == raw_path.lower():
                match = nodes_by_id[nid]
                raw_path = nid
                break
            if raw_path.lower() in nid.lower() or nid.lower() in raw_path.lower():
                suggestions.append(nid)

        if not match:
            return {
                "error": "taxonomy/unknown_path",
                "requested": raw_path,
                "valid_divisions": canonical_divisions,
                "suggestions": suggestions[:5],
            }
        node = match

    node_id = str(node["id"])
    division = node_id.split("/")[0]

    # Direct children from taxonomy
    children = []
    for nid, other in nodes_by_id.items():
        if other.get("parent") == node_id:
            slug = nid.split("/")[-1]
            children.append({"slug": slug, "title": other.get("title", slug), "path": nid})
    children.sort(key=lambda c: c["slug"])

    # Concepts and Wikipedia from vault folder if vault exists
    root = vault_root()
    summary = node.get("scope") or ""
    has_wikipedia = False
    concepts: List[Dict[str, str]] = []

    if root is not None:
        node_dir = root / "wiki" / "library" / "knowledge" / Path(node_id)
        if node_dir.is_dir():
            index_file = node_dir / "index.md"
            if index_file.is_file():
                try:
                    text = index_file.read_text(encoding="utf-8", errors="replace")
                    meta, body = _split_frontmatter(text)
                    if meta.get("description"):
                        summary = meta["description"]
                    else:
                        for p in body.split("\n\n"):
                            clean_p = p.strip()
                            if clean_p and not clean_p.startswith("#"):
                                summary = clean_p[:400]
                                break
                except Exception:
                    pass

            wiki_file = node_dir / "wikipedia.md"
            has_wikipedia = wiki_file.is_file()

            for item in sorted(node_dir.glob("*.md")):
                if item.name in ("index.md", "wikipedia.md") or item.name.startswith("."):
                    continue
                try:
                    c_text = item.read_text(encoding="utf-8", errors="replace")[:1000]
                    c_meta, _ = _split_frontmatter(c_text)
                    c_title = c_meta.get("title") or item.stem.replace("-", " ").title()
                    c_type = c_meta.get("type") or "Concept"
                    concepts.append({
                        "title": c_title,
                        "type": c_type,
                        "path": _relative(item, root),
                    })
                except Exception:
                    continue

    return {
        "path": node_id,
        "title": str(node.get("title") or node_id.split("/")[-1]),
        "division": division,
        "parent": node.get("parent"),
        "summary": summary,
        "children": children,
        "concepts": concepts,
        "listed_under": node.get("listed_under", []),
        "has_wikipedia_article": has_wikipedia,
    }


def clear_cache() -> None:
    with _LOCK:
        _VECTOR_CACHE["key"] = None
        _VECTOR_CACHE["rows"] = []
        _TAXONOMY_CACHE["data"] = None
        _TAXONOMY_CACHE["nodes_by_id"] = {}
