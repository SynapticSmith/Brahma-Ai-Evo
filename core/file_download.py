"""
core/file_download.py
Resumable, verified HTTP downloads shared by every in-app library.

Why this is its own module
--------------------------
It started inside `core/voice_catalog.py`, where the failure modes were first
learned the hard way: `http.client` returns `b""` on a premature EOF instead of
raising, so a truncated body looks exactly like a clean end of stream. That is
why every download here lands in a `.part` file, is compared against a known
size, and is only renamed into place after the size (and digest, when one is
known) matches.

Avatar models need the same guarantees -- a Live2D model is dozens of small
files and a VRM can be 10 MB -- and copying the logic would mean fixing the
same truncation bug twice. So it lives here, engine-agnostic, and
`core/voice_catalog.py` re-exports these names to keep its public surface.

Contract
--------
- `download_file` never raises: it returns `(ok, error)`.
- A cancelled or failed download leaves no file under the final name.
- `cancel` is polled between chunks, so a cancel is responsive but cannot
  corrupt the partial file -- it is removed.
"""
from __future__ import annotations

import hashlib
import os
import time
import urllib.request
from typing import Callable, Optional, Tuple

DEFAULT_USER_AGENT = "Mozilla/5.0 (Brahma Evo file downloader)"


def part_size(part: str) -> int:
    try:
        return os.path.getsize(part)
    except Exception:
        return 0


def discard(path: str) -> None:
    """Remove a file if it exists. Never raises."""
    try:
        os.remove(path)
    except Exception:
        pass


def download_attempt(
    url: str,
    part: str,
    label: str,
    *,
    total: int,
    on_progress: Optional[Callable[[int, int, str], None]],
    cancel: Optional[Callable[[], bool]],
    timeout: float,
    user_agent: str = DEFAULT_USER_AGENT,
) -> Tuple[int, int, str]:
    """One transfer attempt, resuming from whatever is already in `part`.

    Returns (bytes_on_disk, total_bytes, error). `error` is "" when the attempt
    ended cleanly, which does NOT mean it is complete -- the caller compares
    bytes against total, because a dropped connection makes `read()` return an
    empty chunk instead of raising, i.e. a truncated body looks like EOF.
    """
    done = part_size(part)
    if total and done > total:
        discard(part)  # nonsense length: start over rather than trust it
        done = 0

    headers = {"User-Agent": user_agent}
    if done:
        headers["Range"] = f"bytes={done}-"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            if done and status != 206:
                # The server ignored the Range header, so the body starts at
                # byte 0; appending it to the partial file would corrupt it.
                done = 0
            if not total:
                if status == 206:
                    # Content-Range: "bytes 100-999/1000" -> take the /1000.
                    tail = (response.headers.get("Content-Range") or "").rpartition("/")[2]
                    total = int(tail) if tail.isdigit() else 0
                else:
                    try:
                        total = int(response.headers.get("Content-Length") or 0)
                    except Exception:
                        total = 0
            with open(part, "ab" if done else "wb") as out:
                while True:
                    if cancel is not None and cancel():
                        return done, total, "Cancelled."
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if on_progress is not None and total:
                        try:
                            on_progress(done, total, label)
                        except Exception:
                            pass
    except Exception as exc:
        return part_size(part), total, f"{type(exc).__name__}: {exc}"
    return part_size(part), total, ""


def download_file(
    url: str,
    dest: str,
    *,
    expected_size: int = 0,
    expected_md5: str = "",
    expected_sha256: str = "",
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
    timeout: float = 60.0,
    attempts: int = 5,
    user_agent: str = DEFAULT_USER_AGENT,
) -> Tuple[bool, str]:
    """Stream `url` to `dest`, verify it, then atomically move it into place.

    Returns (ok, error). The `.part` suffix is what makes this safe: a partial
    file is never left under the final name, so a discovery layer cannot pick up
    a truncated model and crash on it.

    Large assets routinely lose a connection partway through, and the short
    read is silent, so each attempt resumes with a Range request from the bytes
    already on disk instead of starting over.
    """
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    part = dest + ".part"
    label = os.path.basename(dest)
    total = int(expected_size or 0)
    last_err = ""
    stalled = 0

    for attempt in range(1, max(1, attempts) + 1):
        if cancel is not None and cancel():
            discard(part)
            return False, "Cancelled."
        previous = part_size(part)
        done, total, err = download_attempt(
            url,
            part,
            label,
            total=total,
            on_progress=on_progress,
            cancel=cancel,
            timeout=timeout,
            user_agent=user_agent,
        )
        if err == "Cancelled.":
            discard(part)
            return False, "Cancelled."
        if total and done >= total:
            break
        if not total and not err:
            total = done  # unknown length, clean EOF: trust what arrived
            break
        last_err = err or f"connection closed after {done} of {total} bytes"
        # Only keep retrying while attempts actually gain ground: a server that
        # keeps returning the same short body will never finish, and a model is
        # far too large to re-request on a loop.
        stalled = stalled + 1 if done <= previous else 0
        if stalled >= 2:
            break
        if attempt < attempts:
            time.sleep(min(2.0, 0.5 * attempt))

    done = part_size(part)
    if total and done != total:
        discard(part)
        return False, (
            f"Download failed ({label}): {last_err or 'incomplete'}"
            + (f" (got {done} of {total} bytes)" if total else "")
        )

    if expected_size and done != int(expected_size):
        discard(part)
        return False, (
            f"Size mismatch for {label}: {done} bytes, expected {expected_size}."
        )
    if expected_sha256 or expected_md5:
        expected = (expected_sha256 or expected_md5).lower()
        algorithm = hashlib.sha256 if expected_sha256 else hashlib.md5
        try:
            digest = algorithm()
            with open(part, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
            actual = digest.hexdigest()
        except Exception as exc:
            discard(part)
            return False, f"Could not verify {label}: {exc}"
        if actual != expected:
            discard(part)
            return False, (
                f"Checksum mismatch for {label} "
                f"(downloaded file is corrupt or was tampered with)."
            )

    try:
        os.replace(part, dest)
    except Exception as exc:
        discard(part)
        return False, f"Could not finalise {label}: {exc}"
    return True, ""
