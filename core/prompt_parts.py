"""Two-tier system prompt: a small resident core, a large on-demand manual.

The system prompt used to be one 14.9 KB document. Every OpenAI-compatible
turn re-sent (and every Ollama turn re-prefilled) the whole thing, which on a
CPU-bound local model is most of a turn's latency, and on a custom endpoint is
paid every round for reference material the model may not need.

It is now split in two tiers:

* **Core** (``TIER_CORE``) — behaviour that must be resident every turn:
  identity, how a turn works, principles, the capability inventory and the
  rules for using it, memory doctrine, safety, autonomy and communication.
* **Manual** (``TIER_MANUAL``) — reference material: the per-domain intent
  routing table, the TOOL SURFACE / SKILLS LIBRARY discovery rules and the
  offline/degraded notes. Reachable on demand through the ``get_tool_surface``
  and ``get_skill_manual`` meta-tools, never resident by default.

The two halves live in ``core/prompt.txt`` as one file separated by an exact
``--- MANUAL ---`` marker line, so the text stays editable in one place while
loaders still get one string each. ``core/prompt.txt`` is the single source of
truth and the only file the runtime reads; the manual half stays greppable in
the repo because it is the text below the marker, so no generated second copy
exists to drift out of sync.

Tier selection is one setting, ``prompt_tier`` (``lite`` | ``full`` | ``auto``),
read by :func:`tier_for`. The default is ``lite``: it is the behaviour the
two-tier design was introduced for. ``full`` restores the old single-blob
behaviour; ``auto`` is an explicit opt-in to hardware detection, and resolves
to the full prompt unless the endpoint is on the same machine (loopback), in
which case the smaller core is the faster and safer bet.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

__all__ = [
    "MANUAL_MARKER",
    "TIER_CORE",
    "TIER_FULL",
    "TIER_LITE",
    "TIER_MANUAL",
    "load_prompt_tiers",
    "split_prompt",
    "tier_for",
]

PROMPT_PATH = Path(__file__).resolve().parent / "prompt.txt"

#: The exact line that separates the resident core from the on-demand manual.
MANUAL_MARKER = "--- MANUAL ---"

TIER_CORE = "core"
TIER_MANUAL = "manual"
#: The combined, pre-split prompt.
TIER_FULL = "full"
#: User-facing name for the resident core (the lean setting).
TIER_LITE = "lite"


def split_prompt(text: str) -> "tuple[str, str]":
    """Split one prompt document into ``(core, manual)``.

    The marker line must be alone on its own line. Missing marker or an empty
    core falls back to the whole text as core, so a malformed file degrades to
    the old behaviour instead of an empty system prompt.
    """
    text = text or ""
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() == MANUAL_MARKER:
            core = "\n".join(lines[:idx]).strip()
            manual = "\n".join(lines[idx + 1:]).strip()
            if core:
                return core, manual
            break
    return text.strip(), ""


def load_prompt_tiers(path: Optional[Path] = None) -> "tuple[str, str]":
    """Read the prompt file and return ``(core, manual)``; ``("", "")`` on error."""
    p = Path(path) if path else PROMPT_PATH
    try:
        raw = p.read_text(encoding="utf-8")
    except Exception:
        return "", ""
    return split_prompt(raw)


def _is_loopback(url: str) -> bool:
    host = urlparse((url or "").strip()).hostname
    if not host:
        return False
    if host in ("localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def tier_for(settings: dict, endpoint: str = "") -> str:
    """Resolve the effective tier: ``core`` (lean) or ``full``.

    ``lite`` (the default) keeps only the resident core; ``full`` restores the
    combined prompt; ``auto`` prefers the full prompt but chooses the core when
    the endpoint is loopback, because a same-machine model is the one paying
    the prefill cost. Nothing is lost either way — the manual stays reachable
    through the discovery meta-tools.
    """
    raw = str((settings or {}).get("prompt_tier", TIER_LITE) or TIER_LITE).strip().lower()
    if raw in (TIER_FULL, "combined"):
        return TIER_FULL
    if raw in ("auto", "detect"):
        return TIER_CORE if _is_loopback(endpoint) else TIER_FULL
    if raw in (TIER_CORE, TIER_LITE, "core_only", "small"):
        return TIER_CORE
    if _is_loopback(endpoint):
        return TIER_CORE
    # A typo in a hand-edited setting must not silently inflate every turn's
    # prompt with the manual; fall back to the lean tier.
    return TIER_CORE
