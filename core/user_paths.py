from datetime import datetime, timezone
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

TRUSTED_WORKSPACES_FILENAME = "trusted_workspaces.json"


def get_user_data_dir() -> Path:
    """Per-user data directory for BrahmaAI: ``%LOCALAPPDATA%\\BrahmaAI``.

    Windows is the supported target, so ``LOCALAPPDATA`` is preferred; the
    ``~`` fallback only keeps non-Windows imports (tests, tooling) from
    crashing, and is not a supported layout.
    """
    app_data = os.getenv('LOCALAPPDATA', os.path.expanduser('~'))
    d = Path(app_data) / 'BrahmaAI'
    d.mkdir(parents=True, exist_ok=True)
    return d


def atomic_write_json(path, data, indent: int = 4) -> None:
    """Write ``data`` as JSON to ``path`` without ever exposing a half-written file.

    Settings have more than one writer (the UI cards, ``core/provider_router.py``
    and ``core/instruction_injector.py`` all persist ``app_settings.json``). An
    in-place ``open(path, "w")`` truncates the file first, so a crash -- or a
    reader that arrives mid-write -- sees empty or partial JSON and the settings
    appear to have been reverted. Writing to a sibling temp file and swapping it
    in with :func:`os.replace` makes the update atomic: the target is either the
    old document or the new one, never a fragment.

    The payload is serialized *before* the temp file is created, so a value that
    cannot be encoded leaves the existing file untouched and leaves no temp file
    behind.
    """
    path = Path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(data, indent=indent)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def trusted_workspaces_path(store_path: Optional[str | Path] = None) -> Path:
    """Location of the user-data ``trusted_workspaces.json`` store (ADR-020)."""
    if store_path is not None:
        return Path(store_path)
    return get_user_data_dir() / TRUSTED_WORKSPACES_FILENAME


def normalize_workspace_path(workspace: str | Path) -> str:
    """Canonical path fingerprint for a workspace directory (ADR-020).

    Follows symlinks via :meth:`Path.resolve`, normalizes separators, and folds
    case on Windows so equivalent spellings map to one trust decision.
    """
    try:
        resolved = Path(workspace).expanduser().resolve()
    except OSError:
        resolved = Path(workspace).expanduser()
    return os.path.normcase(os.path.normpath(str(resolved)))


def load_trusted_workspaces(
    store_path: Optional[str | Path] = None,
) -> Dict[str, Dict[str, Any]]:
    """Read the trusted-workspaces store from disk.

    Re-reads on every call so deleting or adding a row takes effect on the next
    turn without a restart. A missing or malformed file fails closed (empty).
    """
    target = trusted_workspaces_path(store_path)
    if not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    entries: Any = raw
    if isinstance(raw, dict) and "workspaces" in raw:
        entries = raw.get("workspaces")

    rows: Dict[str, Dict[str, Any]] = {}
    if isinstance(entries, dict):
        items = list(entries.values())
    elif isinstance(entries, list):
        items = entries
    else:
        return {}

    for item in items:
        if not isinstance(item, dict):
            continue
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        key = normalize_workspace_path(raw_path)
        first_seen = str(item.get("first_seen") or "")
        try:
            policy_version = int(item.get("policy_version", 1))
        except (TypeError, ValueError):
            policy_version = 1
        rows[key] = {
            "path": key,
            "first_seen": first_seen,
            "policy_version": policy_version,
        }
    return rows


def list_trusted_workspaces(
    store_path: Optional[str | Path] = None,
) -> List[Dict[str, Any]]:
    """Return all trusted workspace rows in deterministic order."""
    rows = load_trusted_workspaces(store_path)
    return [rows[key] for key in sorted(rows)]


def is_workspace_trusted(
    workspace: Optional[str | Path],
    *,
    store_path: Optional[str | Path] = None,
) -> bool:
    """True when ``workspace`` is recorded in ``trusted_workspaces.json``."""
    if workspace is None:
        return False
    text = str(workspace).strip()
    if not text:
        return False
    key = normalize_workspace_path(text)
    return key in load_trusted_workspaces(store_path)


def trust_workspace(
    workspace: str | Path,
    *,
    policy_version: int = 1,
    store_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Record ``workspace`` as trusted in user data and return its row."""
    target = trusted_workspaces_path(store_path)
    key = normalize_workspace_path(workspace)
    rows = load_trusted_workspaces(target)
    existing = rows.get(key)
    first_seen = (
        existing["first_seen"]
        if existing and existing.get("first_seen")
        else datetime.now(timezone.utc).isoformat()
    )
    row: Dict[str, Any] = {
        "path": key,
        "first_seen": first_seen,
        "policy_version": int(policy_version),
    }
    rows[key] = row
    atomic_write_json(target, {"version": 1, "workspaces": rows}, indent=2)
    return dict(row)


def revoke_workspace_trust(
    workspace: str | Path,
    *,
    store_path: Optional[str | Path] = None,
) -> bool:
    """Remove ``workspace`` from the trust store. Returns True if a row was removed."""
    target = trusted_workspaces_path(store_path)
    key = normalize_workspace_path(workspace)
    rows = load_trusted_workspaces(target)
    if key not in rows:
        return False
    rows.pop(key, None)
    atomic_write_json(target, {"version": 1, "workspaces": rows}, indent=2)
    return True
