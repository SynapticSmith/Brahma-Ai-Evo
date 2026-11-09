"""Python bridge to the Calyx grounding & provenance engine.

Calyx is an association-native database that measures inputs across independent
frozen lenses (dense embeddings, sparse SPLADE, paraphrase, etc.) and maintains
a hash-chained tamper-evident ledger of every measurement and anchor.

This module provides stdlib-only access to Calyx over its stdio MCP protocol
using :class:`agent.mcp.MCPSession`.
"""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from .mcp import MCPServerConfig, MCPSession

DEFAULT_CALYX_BIN = Path.home() / "Calyx" / "target" / "release" / "calyx-mcp.exe"
DEFAULT_CALYX_HOME = Path.home() / "Calyx" / "home"
DEFAULT_VAULT_NAME = "obsidian-wiki"


def calyx_bin() -> Optional[Path]:
    env_bin = os.getenv("BRAHMA_CALYX_BIN", "").strip()
    if env_bin:
        p = Path(env_bin)
        return p if p.is_file() else None
    return DEFAULT_CALYX_BIN if DEFAULT_CALYX_BIN.is_file() else None


def calyx_home() -> Optional[Path]:
    env_home = os.getenv("BRAHMA_CALYX_HOME", "").strip()
    if env_home:
        p = Path(env_home)
        return p if p.is_dir() else None
    return DEFAULT_CALYX_HOME if DEFAULT_CALYX_HOME.is_dir() else None


def is_available() -> bool:
    """True when the Calyx binary and home directory exist on this machine."""
    b = calyx_bin()
    h = calyx_home()
    return b is not None and h is not None and b.is_file() and h.is_dir()


def _server_config() -> MCPServerConfig:
    b = calyx_bin()
    h = calyx_home()
    if not b or not h:
        raise RuntimeError("Calyx binary or home directory is missing.")
    return MCPServerConfig(
        name="calyx",
        command=str(b),
        args=(),
        env={"CALYX_HOME": str(h)},
        timeout=60.0,
    )


@contextmanager
def calyx_session() -> Generator[MCPSession, None, None]:
    """Scoped MCP session connected to the local Calyx engine."""
    config = _server_config()
    session = MCPSession()
    session.start([config])
    try:
        yield session
    finally:
        session.close()


def _call(tool_name: str, arguments: Dict[str, Any]) -> Any:
    """Execute a single Calyx MCP tool call."""
    with calyx_session() as session:
        # Tool names are registered in session as mcp_calyx_<sanitized_tool>
        # where dots are converted to underscores.
        sanitized = re.sub(r"[^A-Za-z0-9_]", "_", tool_name)
        target = f"mcp_calyx_{sanitized}"
        if target not in session.tools:
            alt = f"mcp_calyx_calyx_{sanitized}"
            if alt in session.tools:
                target = alt
            else:
                # Strip leading calyx_ if present
                clean = re.sub(r"^calyx_", "", sanitized)
                target = f"mcp_calyx_{clean}" if f"mcp_calyx_{clean}" in session.tools else f"mcp_calyx_calyx_{clean}"
        raw = session.call(target, arguments)
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            return raw


def status(vault: str = DEFAULT_VAULT_NAME) -> Dict[str, Any]:
    """Inspect the Calyx vault: abundance, kernel recall, chain integrity."""
    if not is_available():
        return {"available": False, "vault": vault}
    out: Dict[str, Any] = {
        "available": True,
        "vault": vault,
        "bin": str(calyx_bin()),
        "home": str(calyx_home()),
    }
    try:
        abundance = _call("calyx.abundance", {"vault": vault})
        out["abundance"] = abundance if isinstance(abundance, dict) else {}
    except Exception as exc:
        out["abundance_error"] = str(exc)

    try:
        kernel = _call("calyx.kernel", {"vault": vault})
        out["kernel"] = kernel if isinstance(kernel, dict) else {}
    except Exception as exc:
        out["kernel_error"] = str(exc)

    try:
        chain = _call("calyx.verify_chain", {"vault": vault})
        out["chain"] = chain if isinstance(chain, dict) else {}
    except Exception as exc:
        out["chain_error"] = str(exc)

    return out


def search(
    query: str,
    k: int = 5,
    vault: str = DEFAULT_VAULT_NAME,
) -> List[Dict[str, Any]]:
    """Search Calyx constellations with provenance attached."""
    if not is_available():
        return []
    clean_query = str(query or "").strip()
    if not clean_query:
        return []
    try:
        res = _call("calyx.search", {"query": clean_query, "k": k, "vault": vault})
        if isinstance(res, dict) and "hits" in res and isinstance(res["hits"], list):
            return res["hits"]
    except Exception:
        pass
    return []


def kernel_answer(
    query: str,
    vault: str = DEFAULT_VAULT_NAME,
) -> Dict[str, Any]:
    """Synthesize a high-level answer from the Calyx kernel."""
    if not is_available():
        return {}
    clean_query = str(query or "").strip()
    if not clean_query:
        return {}
    try:
        res = _call("calyx.kernel_answer", {"query": clean_query, "vault": vault})
        return res if isinstance(res, dict) else {}
    except Exception:
        return {}


def extract_ingest_text(markdown_text: str, max_chars: int = 2_000) -> Tuple[str, str, str]:
    """Extract (title, description, body_excerpt) from an OKF/standard markdown note."""
    text = str(markdown_text or "").strip()
    title = ""
    description = ""

    # Parse YAML frontmatter if present
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm = parts[1]
            body = parts[2].strip()
            for line in fm.splitlines():
                if line.startswith("title:"):
                    title = line.split("title:", 1)[1].strip().strip('"\'')
                elif line.startswith("description:"):
                    description = line.split("description:", 1)[1].strip().strip('"\'')
            text = body

    if not title:
        match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        if match:
            title = match.group(1).strip()

    # Strip markdown headers and collapse blank lines for the body excerpt
    clean_body = re.sub(r"#+\s+", "", text)
    clean_body = re.sub(r"\n{3,}", "\n\n", clean_body).strip()
    return title, description, clean_body[:max_chars]


def ingest_note(
    rel_path: str,
    raw_markdown: str,
    vault: str = DEFAULT_VAULT_NAME,
    anchor: bool = True,
) -> Dict[str, Any]:
    """Ingest one Obsidian note into Calyx and anchor it as a ground truth label."""
    title, desc, body = extract_ingest_text(raw_markdown)
    header = f"[{rel_path}] {title}" if title else f"[{rel_path}]"
    if desc:
        header = f"{header}: {desc}"
    composed = f"{header}\n\n{body}".strip()

    with calyx_session() as session:
        # Ingest call
        ingest_raw = session.call(
            "mcp_calyx_calyx_ingest",
            {"input": composed, "vault": vault},
        )
        try:
            res = json.loads(ingest_raw)
        except Exception:
            return {"ok": False, "error": str(ingest_raw)}

        cx_id = res.get("cx_id")
        out: Dict[str, Any] = {"ok": True, "cx_id": cx_id, "new": res.get("new", False)}

        if anchor and cx_id:
            try:
                anchor_raw = session.call(
                    "mcp_calyx_calyx_anchor",
                    {
                        "cx_id": cx_id,
                        "kind": "label",
                        "label": rel_path,
                        "value": True,
                        "vault": vault,
                    },
                )
                out["anchor"] = json.loads(anchor_raw) if isinstance(anchor_raw, str) else anchor_raw
            except Exception as exc:
                out["anchor_error"] = str(exc)

        return out
