import json
import os
import tempfile
from pathlib import Path

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
