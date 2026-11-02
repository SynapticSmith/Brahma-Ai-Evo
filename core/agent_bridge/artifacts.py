"""Durable, referencable agent output store and retrieval (ADR-015, Q2).

Persists all external agent call outputs under:
    %LOCALAPPDATA%\\BrahmaAI\\agent_artifacts\\<agent-id>\\<call-id>.json
with an append-only manifest.jsonl index, capping in-context model returns at
MAX_AGENT_OUTPUT_BYTES while providing the canonical read_agent_artifact tool
for pagination and full provenance retrieval.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

from agent.spec import ToolContext, ToolSpec
from core.agent_bridge.constants import (
    ARTIFACT_REF_PREFIX,
    ARTIFACTS_ROOT,
    MAX_AGENT_OUTPUT_BYTES,
)
from core.agent_bridge.models import AgentCallResult
from core.user_paths import atomic_write_json, get_user_data_dir

CANONICAL_REF_PREFIX = "agent-artifact://"
ALT_REF_PREFIX = ARTIFACT_REF_PREFIX  # "agent-out://"
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")
_manifest_lock = threading.Lock()


def default_artifacts_dir() -> Path:
    """Return root directory for agent artifacts."""
    env = os.getenv("BRAHMA_AGENT_ARTIFACTS_DIR")
    if env:
        d = Path(env).expanduser().resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d
    d = get_user_data_dir() / ARTIFACTS_ROOT
    d.mkdir(parents=True, exist_ok=True)
    return d


def sanitize_identifier(raw: str) -> str:
    """Validate and sanitize an agent_id or call_id to prevent directory traversal."""
    cleaned = str(raw or "").strip()
    if not cleaned or not _SAFE_ID_RE.match(cleaned) or ".." in cleaned:
        raise ValueError(f"Invalid identifier for agent artifact path: {raw!r}")
    return cleaned


def parse_artifact_ref(ref: str) -> tuple[str, str]:
    """Parse artifact reference into (agent_id, call_id).
    
    Accepts:
        agent-artifact://<agent-id>/<call-id>
        agent-out://<agent-id>/<call-id>
    """
    cleaned = str(ref or "").strip()
    prefix = None
    if cleaned.startswith(CANONICAL_REF_PREFIX):
        prefix = CANONICAL_REF_PREFIX
    elif cleaned.startswith(ALT_REF_PREFIX):
        prefix = ALT_REF_PREFIX
    else:
        raise ValueError(
            f"Invalid artifact reference scheme in {ref!r}; "
            f"expected prefix '{CANONICAL_REF_PREFIX}' or '{ALT_REF_PREFIX}'"
        )

    remainder = cleaned[len(prefix):].strip("/")
    parts = remainder.split("/")
    if len(parts) != 2:
        raise ValueError(f"Malformed artifact reference {ref!r}; expected '<agent-id>/<call-id>'")

    agent_id = sanitize_identifier(parts[0])
    call_id = sanitize_identifier(parts[1])
    return agent_id, call_id


def save_artifact(
    agent_id: str,
    verb: str,
    output: str,
    *,
    ok: bool = True,
    call_id: Optional[str] = None,
    error: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    artifacts_dir: Optional[Path] = None,
) -> str:
    """Persist agent output to disk and record in manifest.jsonl.
    
    Returns the canonical artifact reference (agent-artifact://<agent-id>/<call-id>).
    """
    safe_agent = sanitize_identifier(agent_id)
    if not call_id:
        call_id = f"call_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    safe_call = sanitize_identifier(call_id)

    root = artifacts_dir if artifacts_dir is not None else default_artifacts_dir()
    agent_dir = root / safe_agent
    agent_dir.mkdir(parents=True, exist_ok=True)

    out_text = str(output or "")
    out_bytes = out_text.encode("utf-8")
    bytes_len = len(out_bytes)
    sha256_hash = hashlib.sha256(out_bytes).hexdigest()
    ts = time.time()
    truncated = bytes_len > MAX_AGENT_OUTPUT_BYTES

    artifact_ref = f"{CANONICAL_REF_PREFIX}{safe_agent}/{safe_call}"

    record = {
        "call_id": safe_call,
        "agent_id": safe_agent,
        "verb": verb,
        "ok": ok,
        "ts": ts,
        "bytes": bytes_len,
        "sha256": sha256_hash,
        "truncated": truncated,
        "error": error,
        "metadata": dict(metadata or {}),
        "output": out_text,
    }

    # 1. Atomic write of full JSON record
    target_file = agent_dir / f"{safe_call}.json"
    atomic_write_json(target_file, record, indent=2)

    # 2. Append manifest summary line
    manifest_file = agent_dir / "manifest.jsonl"
    manifest_line = {
        "call_id": safe_call,
        "agent_id": safe_agent,
        "verb": verb,
        "ok": ok,
        "ts": ts,
        "bytes": bytes_len,
        "sha256": sha256_hash,
        "truncated": truncated,
        "artifact_ref": artifact_ref,
    }
    with _manifest_lock:
        with open(manifest_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(manifest_line) + "\n")

    return artifact_ref


def read_artifact(
    ref: str,
    *,
    offset: int = 0,
    limit: Optional[int] = 4000,
    artifacts_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Retrieve full or sliced output from an artifact reference.
    
    Guards strictly against directory traversal.
    """
    agent_id, call_id = parse_artifact_ref(ref)
    root = artifacts_dir if artifacts_dir is not None else default_artifacts_dir()

    target_file = (root / agent_id / f"{call_id}.json").resolve()
    # Path confinement check
    expected_parent = (root / agent_id).resolve()
    if target_file.parent != expected_parent or not str(target_file).startswith(str(root.resolve())):
        raise PermissionError(f"Artifact path traversal denied: {ref}")

    if not target_file.is_file():
        raise FileNotFoundError(f"Artifact not found: {ref}")

    try:
        content = json.loads(target_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise OSError(f"Corrupt artifact {ref}: {exc}") from exc

    full_output = str(content.get("output") or "")
    total_chars = len(full_output)

    offset = max(0, int(offset or 0))
    if limit is not None:
        limit = max(1, min(int(limit), 20000))
        end = offset + limit
        output_slice = full_output[offset:end]
    else:
        output_slice = full_output[offset:]

    has_more = (offset + len(output_slice)) < total_chars

    return {
        "ref": ref,
        "call_id": call_id,
        "agent_id": agent_id,
        "verb": content.get("verb", ""),
        "ok": content.get("ok", True),
        "ts": content.get("ts", 0.0),
        "bytes_total": content.get("bytes", len(full_output.encode("utf-8"))),
        "chars_total": total_chars,
        "sha256": content.get("sha256", ""),
        "offset": offset,
        "limit": limit,
        "slice_chars": len(output_slice),
        "has_more": has_more,
        "output": output_slice,
    }


def list_artifacts(
    agent_id: Optional[str] = None,
    *,
    limit: int = 50,
    artifacts_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """List recent artifact records from manifest index, newest first."""
    root = artifacts_dir if artifacts_dir is not None else default_artifacts_dir()
    entries: List[Dict[str, Any]] = []

    if agent_id:
        safe_agent = sanitize_identifier(agent_id)
        manifests = [root / safe_agent / "manifest.jsonl"]
    else:
        if not root.is_dir():
            return []
        manifests = list(root.glob("*/manifest.jsonl"))

    for mf in manifests:
        if not mf.is_file():
            continue
        try:
            with open(mf, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except Exception:
                            continue
        except Exception:
            continue

    # Sort descending by timestamp
    entries.sort(key=lambda x: x.get("ts", 0.0), reverse=True)
    return entries[: max(1, limit)]


def clean_expired_artifacts(max_days: int, artifacts_dir: Optional[Path] = None) -> int:
    """Delete artifact files older than max_days. Returns count of deleted files."""
    if max_days <= 0:
        return 0

    root = artifacts_dir if artifacts_dir is not None else default_artifacts_dir()
    if not root.is_dir():
        return 0

    cutoff = time.time() - (max_days * 86400.0)
    deleted_count = 0

    for agent_dir in root.iterdir():
        if not agent_dir.is_dir():
            continue
        for file_path in agent_dir.glob("*.json"):
            try:
                if file_path.stat().st_mtime < cutoff:
                    file_path.unlink(missing_ok=True)
                    deleted_count += 1
            except OSError:
                continue

    return deleted_count


def persist_call_result(
    agent_id: str,
    verb: str,
    result: AgentCallResult,
    *,
    call_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    artifacts_dir: Optional[Path] = None,
    max_output_bytes: int = MAX_AGENT_OUTPUT_BYTES,
) -> AgentCallResult:
    """Persist AgentCallResult output to artifact store and attach reference.
    
    If the output exceeds max_output_bytes, result.output is safely truncated
    and annotated with the artifact reference and pagination instructions.
    """
    raw_output = str(result.output or "")
    total_bytes = len(raw_output.encode("utf-8"))
    result.bytes_total = total_bytes

    # Persist the full raw output before truncation (ADR-015)
    artifact_ref = save_artifact(
        agent_id=agent_id,
        verb=verb,
        output=raw_output,
        ok=result.ok,
        call_id=call_id,
        error=result.error,
        metadata=metadata,
        artifacts_dir=artifacts_dir,
    )
    result.artifact_ref = artifact_ref

    if total_bytes > max_output_bytes:
        result.truncated = True
        # UTF-8 safe slice
        slice_text = raw_output.encode("utf-8")[:max_output_bytes].decode("utf-8", errors="ignore")
        result.output = (
            f"{slice_text}\n\n"
            f"[Output truncated: {total_bytes:,} bytes total exceeds budget {max_output_bytes:,} bytes.\n"
            f"Full output stored at: {artifact_ref}\n"
            f"Use tool read_agent_artifact(ref='{artifact_ref}', offset=..., limit=...) to inspect.]"
        )
    return result


def create_read_artifact_spec(artifacts_dir: Optional[Path] = None) -> ToolSpec:
    """Create the canonical read_agent_artifact ToolSpec (ADR-015)."""
    def _handler(args: dict, context: ToolContext) -> str:
        ref = str(args.get("ref") or "").strip()
        if not ref:
            return "Error: 'ref' parameter is required."

        offset = int(args.get("offset") or 0)
        limit = int(args.get("limit") or 4000)

        try:
            res = read_artifact(ref, offset=offset, limit=limit, artifacts_dir=artifacts_dir)
        except Exception as exc:
            return f"Error reading artifact {ref}: {exc}"

        header = (
            f"Artifact: {res['ref']}\n"
            f"Agent: {res['agent_id']} | Verb: {res['verb']} | SHA256: {res['sha256'][:16]}...\n"
            f"Total chars: {res['chars_total']:,} | Slice: [{res['offset']}:{res['offset'] + res['slice_chars']}] "
            f"({res['slice_chars']:,} chars)\n"
            f"{'-' * 60}\n"
        )
        footer = ""
        if res["has_more"]:
            next_offset = res["offset"] + res["slice_chars"]
            remaining = res["chars_total"] - next_offset
            footer = (
                f"\n{'-' * 60}\n"
                f"[{remaining:,} chars remaining. Use read_agent_artifact(ref='{ref}', "
                f"offset={next_offset}, limit={res['limit']}) to read next chunk.]"
            )
        return f"{header}{res['output']}{footer}"

    return ToolSpec(
        name="read_agent_artifact",
        description=(
            "Read persisted output from an external agent call by artifact reference "
            "(agent-artifact://<agent-id>/<call-id>). Use offset and limit to inspect "
            "large outputs safely without blowing the context window."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "Artifact reference URI (e.g. agent-artifact://claude-code/call_12345678)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Character offset from start of output (default: 0)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of characters to return (default: 4000, max: 20000)",
                },
            },
            "required": ["ref"],
        },
        tool_class="read",
        handler=_handler,
        requires_approval=False,
    )
