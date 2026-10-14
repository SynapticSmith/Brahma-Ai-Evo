"""
fetch_live2d_model.py

Install a Live2D sample model plus its licence into the per-user data folder.

Licence reality, checked rather than assumed (2026-10-14)
-------------------------------------------------------
No CC0 or CC-BY Live2D Cubism model exists that could be found. Every model in
circulation is either Live2D's own sample data under the "Free Material
License", or a third-party VTuber's model that its creator has restricted.

Live2D's Free Material License is the closest thing to permissive that exists,
and Live2D's own wording (https://www.live2d.com/en/learn/sample/simple-model)
says a General User or Small-Scale Enterprise (annual sales under 10 million
JPY) may use the sample materials for creative activities, commercial and
non-commercial alike. That is a permissive grant. It is *not* an open-source
licence, and it is not CC0/CC-BY, which is why this script installs the licence
text next to the model instead of just dropping the files.

The runtime is a separate problem and this script does not solve it:
`live2dcubismcore.min.js` and `framework.js` come from the Cubism SDK for Web,
which Live2D distributes after you accept their EULA. They are deliberately not
present in Live2D/CubismWebSamples on GitHub (only Core/CHANGELOG.md,
Core/LICENSE.md and Core/RedistributableFiles.txt are), so there is nothing to
mirror. Download them yourself from
https://www.live2d.com/en/sdk/download/web/ into
`assets/avatar_live2d/vendor/`.

Usage:  py -3.11 fetch_live2d_model.py [--model Ren] [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

UPSTREAM = "Live2D/CubismWebSamples"
BRANCH = "develop"
MODEL_ROOT_URL = (
    f"https://raw.githubusercontent.com/{UPSTREAM}/{BRANCH}/Samples/Resources"
)
API_URL = f"https://api.github.com/repos/{UPSTREAM}/contents/Samples/Resources"
API_TREE_URL = f"https://api.github.com/repos/{UPSTREAM}/git/trees/{BRANCH}?recursive=1"

# Downloaded next to the model so the grant travels with the files.
LICENCE_URLS = {
    "LICENSE-Free-Material-License.txt":
        "https://www.live2d.com/eula/live2d-free-material-license-agreement_en.html",
    "LICENSE-Sample-Data-Terms-of-Use.txt":
        "https://www.live2d.com/en/learn/sample/model-terms/",
}

USER_AGENT = "brahma-one-391-avatar-installer"
DEFAULT_MODEL = "Ren"


def _get(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _strip_html(raw: bytes) -> str:
    """Licence pages are HTML; a licence file full of markup is not a licence."""
    import html
    import re

    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h\d|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def model_files(name: str) -> list:
    tree = json.loads(_get(API_TREE_URL).decode("utf-8"))
    prefix = f"Samples/Resources/{name}/"
    out = []
    for node in tree.get("tree", []):
        if node.get("type") != "blob" or not node["path"].startswith(prefix):
            continue
        rel = node["path"][len(prefix):]
        # Source maps and docs are not needed to run the model.
        if rel.endswith((".map", ".md")) or "/.git" in rel:
            continue
        out.append((rel, node.get("size", 0)))
    return sorted(out)


def install(name: str, dest_root: Path, force: bool = False) -> int:
    dest = dest_root / name
    if dest.exists() and any(dest.iterdir()) and not force:
        print(f"[skip] {dest} already populated (use --force to refetch)")
        return 0
    dest.mkdir(parents=True, exist_ok=True)

    files = model_files(name)
    if not files:
        print(f"[fail] upstream has no files for model {name!r}")
        return 1

    manifest, total = [], 0
    for rel, expected in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = _get(f"{MODEL_ROOT_URL}/{name}/{rel}")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            print(f"[fail] {rel}: {exc}")
            return 1
        if not data:
            print(f"[fail] {rel}: empty response")
            return 1
        target.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        manifest.append({"file": rel, "bytes": len(data), "sha256": digest})
        total += len(data)
        print(f"  {rel:42} {len(data)//1024:>6} KB  {digest[:12]}")

    # The licence travels with the model, per the install instruction.
    for name_, url in LICENCE_URLS.items():
        try:
            body = _strip_html(_get(url))
            header = (
                f"Live2D {name_}\n"
                f"Source: {url}\n"
                f"Retrieved: {__import__('datetime').datetime.now().isoformat(timespec='seconds')}\n"
                f"Applies to: {name} model from {UPSTREAM}@{BRANCH}\n"
                f"{'-' * 78}\n\n"
            )
            (dest / name_).write_text(header + body, encoding="utf-8")
            print(f"  {name_:42} {len(body)//1024:>6} KB  (licence)")
        except Exception as exc:  # noqa: BLE001 - report, do not abort the install
            print(f"[warn] could not store {name_}: {exc}")

    provenance = {
        "model": name,
        "source_repo": f"https://github.com/{UPSTREAM}",
        "branch": BRANCH,
        "path": f"Samples/Resources/{name}",
        "license": "Live2D Free Material License",
        "license_url": LICENCE_URLS["LICENSE-Free-Material-License.txt"],
        "total_bytes": total,
        "files": manifest,
    }
    (dest / "PROVENANCE.json").write_text(
        json.dumps(provenance, indent=2), encoding="utf-8")
    print(f"[ok] {name}: {len(manifest)} files, {total//1024} KB -> {dest}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--root", default=None)
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else (
        Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BrahmaAI" / "avatar" / "live2d")
    print(f"[info] installing Live2D model {args.model!r} into {root}")
    return install(args.model, root, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())