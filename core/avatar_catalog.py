"""
core/avatar_catalog.py
Browse, install and import avatar assets - Live2D models, VRM humanoids and
3D backgrounds - from inside the app instead of running a fetch script.

Three very different sources, one rule
--------------------------------------
The rule is the same one `fetch_live2d_model.py` and `fetch_vrm_model.py`
already live by: **only offer what the licence actually allows, and verify it
rather than trusting a README.**

live2d   Live2D's own CubismWebSamples. No CC0/CC-BY Cubism model exists, so
         the only honest catalogue is Live2D's sample data under the Free
         Material License. Each file is sha256-verified against jsDelivr's
         published file list, which is the same evidence the CDN serves.
         The Cubism *runtime* is proprietary and cannot be redistributed, so
         this module never downloads it; the UI explains that instead.

vrm      There is no "all VRM" catalogue, because every VRM carries its own
         permission metadata. So the catalogue is a short list of models whose
         files were checked, one by one, to declare redistribution + commercial
         use. The checksum is pinned, and the file is re-checked *after*
         download - a catalogue entry is a claim, the file is the evidence.
         Anything else is imported by the user, with its verdict shown.

backgrounds
         Two kinds of file, both just folders on disk:
           scene.glb / scene.gltf (+ .bin + textures)  a 3D environment
           panorama.hdr / .jpg / .png                    a 360 skybox
         Users can import their own, or download a CC0 HDRI from Poly Haven.
         Poly Haven's API publishes an md5 and size per file, so those
         downloads are verified against upstream metadata rather than a digest
         we pinned ourselves.

Design rules
------------
- No Qt. Pure data + `urllib`; the UI layer owns threads and widgets.
- Nothing here raises: every entry point returns a result or (ok, error).
- Installs are atomic. A Live2D model is downloaded into `<id>.installing`
  and only renamed into place when the whole folder verifies, so a half-model
  can never be discovered by the renderer. A VRM lands in a `.part` file and
  is renamed only after the digest and licence checks pass.
- Background folders are scanned, never trusted: a file is only selectable
  after it parses as GLB/glTF/HDR/JPEG/PNG, so a bad download cannot become
  a black window the user cannot explain.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import struct
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import file_download
from core.user_paths import get_user_data_dir

USER_AGENT = "Mozilla/5.0 (Brahma Evo avatar catalog)"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60

ProgressFn = Optional[Callable[[int, int, str], None]]
CancelFn = Optional[Callable[[], bool]]


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def avatar_root() -> str:
    """`%LOCALAPPDATA%\\BrahmaAI\\avatar`, the root the fetch scripts install to."""
    return str(get_user_data_dir() / "avatar")


def live2d_dir() -> str:
    return os.path.join(avatar_root(), "live2d")


def vrm_dir() -> str:
    return os.path.join(avatar_root(), "vrm")


def backgrounds_dir() -> str:
    """Where file-based backgrounds live.

    Sibling of `avatar/` rather than inside it: a background is a scene, not a
    character, and the two lists are never merged.
    """
    return str(get_user_data_dir() / "backgrounds")


def _cache_dir() -> str:
    path = os.path.join(backgrounds_dir(), "_cache")
    os.makedirs(path, exist_ok=True)
    return path


def _ensure(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _http_json(url: str, timeout: float = 45.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _cached_json(url: str, filename: str, ttl: float = CACHE_TTL_SECONDS) -> Any:
    """Fetch JSON with an on-disk cache, falling back to stale data offline.

    Network failure returns the stale copy when one exists; that is what keeps
    the dialog rendering a usable list on a plane instead of an empty page.
    """
    path = os.path.join(_cache_dir(), filename)
    fresh = False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cached = json.load(fh)
        fresh = (time.time() - float(cached.get("fetched_at") or 0)) < ttl
        if fresh:
            return cached.get("data")
    except Exception:
        cached = None
    try:
        data = _http_json(url)
    except Exception:
        return cached.get("data") if cached else None
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": time.time(), "url": url, "data": data}, fh)
    except Exception:
        pass
    return data


def _sha256_b64(value: str) -> str:
    """jsDelivr publishes base64 digests; the downloader wants hex."""
    try:
        return base64.b64decode(value).hex()
    except Exception:
        return ""


def _slugify(name: str, fallback: str = "model") -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "")).strip("-._")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or fallback


def _unique_dir(parent: str, slug: str) -> str:
    candidate = os.path.join(parent, slug)
    if not os.path.exists(candidate):
        return candidate
    for i in range(2, 1000):
        candidate = os.path.join(parent, f"{slug}-{i}")
        if not os.path.exists(candidate):
            return candidate
    return os.path.join(parent, f"{slug}-{int(time.time())}")


def _dir_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except Exception:
                pass
    return total


def _write_json(path: str, data: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Live2D catalogue
# ---------------------------------------------------------------------------
LIVE2D_REPO = "Live2D/CubismWebSamples"
LIVE2D_REF = "develop"
LIVE2D_TREE_URL = (
    f"https://data.jsdelivr.com/v1/packages/gh/{LIVE2D_REPO}"
    f"@{LIVE2D_REF}?structure=flat"
)
LIVE2D_CDN = f"https://cdn.jsdelivr.net/gh/{LIVE2D_REPO}@{LIVE2D_REF}"
LIVE2D_RESOURCES = "/Samples/Resources/"
LIVE2D_LICENSE_URL = "https://www.live2d.com/en/learn/sample/simple-model"

LIVE2D_NOTES: Dict[str, str] = {
    "Haru": "Live2D's flagship sample: two outfits and a full expression set.",
    "Hiyori": "The classic Hiyori sample, with motions and expressions.",
    "Mao": "Mao, a newer sample with a full motion set.",
    "Mark": "Simple male sample, small and fast to load.",
    "Natori": "Natori, with several expressions and motions.",
    "Ren": "Ren sample, compact motion set.",
    "Rice": "Chibi rice-ball character, playful and small.",
    "Wanko": "Wanko the dog, the smallest sample.",
}


def _live2d_tree(force: bool = False) -> List[Dict[str, Any]]:
    if force:
        try:
            os.remove(os.path.join(_cache_dir(), "live2d-tree.json"))
        except Exception:
            pass
    data = _cached_json(LIVE2D_TREE_URL, "live2d-tree.json")
    if not isinstance(data, dict):
        return []
    files = data.get("files")
    return files if isinstance(files, list) else []


def live2d_catalog(force: bool = False) -> List[Dict[str, Any]]:
    """Every installable sample model, grouped from the CDN's file list.

    The tree lists every file in the repository; a "model" is a directory under
    `Samples/Resources/` that contains a `*.model3.json`. Grouping here rather
    than hard-coding eight names means a new upstream sample appears in the
    dialog without a code change.
    """
    files = _live2d_tree(force=force)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for f in files:
        name = str(f.get("name") or "")
        if not name.startswith(LIVE2D_RESOURCES):
            continue
        rest = name[len(LIVE2D_RESOURCES):]
        if "/" not in rest:
            continue  # a loose png at the Resources root, not a model
        model, _slash, inner = rest.partition("/")
        if not model or model.startswith("."):
            continue
        grouped.setdefault(model, []).append({
            "path": inner,
            "repo_path": name,
            "size": int(f.get("size") or 0),
            "sha256": _sha256_b64(str(f.get("hash") or "")),
        })

    out: List[Dict[str, Any]] = []
    for model, entries in grouped.items():
        if not any(e["path"].endswith(".model3.json") for e in entries):
            continue
        total = sum(e["size"] for e in entries)
        out.append({
            "engine": "live2d",
            "id": model,
            "name": model,
            "note": LIVE2D_NOTES.get(model, "Live2D sample model."),
            "files": sorted(entries, key=lambda e: e["path"]),
            "bytes": total,
            "count": len(entries),
        })
    out.sort(key=lambda e: e["name"].lower())
    return out


def _live2d_notice(entry: Dict[str, Any]) -> str:
    return (
        f"{entry.get('name')} - Live2D sample material\n"
        f"========================================\n\n"
        f"Source : https://github.com/{LIVE2D_REPO}\n"
        f"Licence: Live2D Free Material License\n"
        f"         {LIVE2D_LICENSE_URL}\n\n"
        "Live2D's Free Material License permits use by a General User or a\n"
        "Small-Scale Enterprise (annual sales under 10 million JPY), commercial\n"
        "and non-commercial alike. It is not an open-source licence, so this\n"
        "notice is installed next to the model rather than dropping the files\n"
        "without context.\n\n"
        "The Cubism runtime (live2dcubismcore.min.js) is NOT part of this\n"
        "download: it is proprietary and cannot be redistributed. Brahma Evo\n"
        "ships it in assets/avatar_live2d/vendor/; on a fresh checkout it has\n"
        "to be placed there by hand from the Live2D SDK.\n\n"
        f"Installed {_stamp()} by Brahma Evo.\n"
    )


def install_live2d(
    entry_or_id: Any,
    on_progress: ProgressFn = None,
    cancel: CancelFn = None,
) -> Tuple[bool, str]:
    """Download one Live2D model folder and verify every file against the CDN.

    A model is a directory of dozens of files, so the install is staged in
    `<id>.installing` and only renamed when every file has matched its size and
    sha256. The renderer scans `live2d/` for model folders, so it can never see
    a half-installed one.
    """
    entry = _find_live2d(entry_or_id)
    if not entry:
        return False, "That Live2D model is not in the catalogue."
    dest = os.path.join(live2d_dir(), entry["id"])
    if os.path.isdir(dest):
        if _has_live2d_model(dest):
            return False, f"{entry['name']} is already installed."
        # A folder with no model file cannot be used by anything, and leaving
        # it in place would make the atomic rename below fail on Windows.
        shutil.rmtree(dest, ignore_errors=True)
    tmp = dest + ".installing"
    _ensure(live2d_dir())

    total = int(entry.get("bytes") or 0)
    done_before = 0
    last_err = ""

    for f in entry.get("files") or []:
        if cancel is not None and cancel():
            shutil.rmtree(tmp, ignore_errors=True)
            return False, "Cancelled."
        rel = str(f.get("path") or "").replace("/", os.sep)
        target = os.path.join(tmp, rel)
        url = LIVE2D_CDN + str(f.get("repo_path") or "")

        def _progress(done: int, _total: int, label: str, _base: int = done_before) -> None:
            if on_progress is not None:
                try:
                    on_progress(_base + done, total, label)
                except Exception:
                    pass

        ok, err = file_download.download_file(
            url,
            target,
            expected_size=int(f.get("size") or 0),
            expected_sha256=str(f.get("sha256") or ""),
            on_progress=_progress,
            cancel=cancel,
            timeout=60.0,
            user_agent=USER_AGENT,
        )
        if not ok:
            last_err = err
            break
        done_before += int(f.get("size") or 0)

    if last_err:
        # Not a cancel: keep the staging folder so a retry resumes the file it
        # stopped on instead of re-fetching the whole model.
        return False, f"Could not install {entry['name']}: {last_err}"

    _write_json(os.path.join(tmp, "PROVENANCE.json"), {
        "kind": "live2d",
        "name": entry["name"],
        "source": f"https://github.com/{LIVE2D_REPO}",
        "catalogue": "Live2D CubismWebSamples / Samples/Resources",
        "licence": "Live2D Free Material License",
        "licence_url": LIVE2D_LICENSE_URL,
        "installed_at": _stamp(),
        "files": [
            {"path": f["path"], "bytes": f["size"], "sha256": f["sha256"]}
            for f in entry.get("files") or []
        ],
    })
    try:
        with open(os.path.join(tmp, "LICENSE-NOTICE.txt"), "w", encoding="utf-8") as fh:
            fh.write(_live2d_notice(entry))
    except Exception:
        pass

    model_file = _find_live2d_model_file(tmp)
    if not model_file:
        shutil.rmtree(tmp, ignore_errors=True)
        return False, "The download completed but contains no .model3.json."
    try:
        os.replace(tmp, dest)
    except Exception as exc:
        return False, f"Could not finalise {entry['name']}: {exc}"
    if on_progress is not None:
        try:
            on_progress(total, total, entry["name"])
        except Exception:
            pass
    return True, ""


def _find_live2d(entry_or_id: Any) -> Optional[Dict[str, Any]]:
    if isinstance(entry_or_id, dict):
        return entry_or_id
    wanted = str(entry_or_id or "")
    for entry in live2d_catalog():
        if entry["id"] == wanted:
            return entry
    return None


def _find_live2d_model_file(folder: str) -> str:
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if f.endswith(".model3.json"):
                return os.path.join(root, f)
    return ""


def _has_live2d_model(folder: str) -> bool:
    return bool(_find_live2d_model_file(folder))


def installed_live2d() -> List[Dict[str, Any]]:
    """Model folders already on disk, including ones the user copied in."""
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(live2d_dir()):
        return out
    for name in sorted(os.listdir(live2d_dir())):
        if name.startswith(".") or name.endswith(".installing"):
            continue
        folder = os.path.join(live2d_dir(), name)
        if not os.path.isdir(folder):
            continue
        model_file = _find_live2d_model_file(folder)
        if not model_file:
            continue
        provenance = {}
        try:
            with open(os.path.join(folder, "PROVENANCE.json"), "r", encoding="utf-8") as fh:
                provenance = json.load(fh)
        except Exception:
            pass
        out.append({
            "id": name,
            "name": os.path.basename(model_file)[:-len(".model3.json")] or name,
            "folder": folder,
            "model_file": model_file,
            "bytes": _dir_bytes(folder),
            "provenance": provenance if isinstance(provenance, dict) else {},
        })
    return out


def delete_live2d(name: str) -> Tuple[bool, str]:
    folder = os.path.join(live2d_dir(), os.path.basename(str(name or "")))
    if not os.path.isdir(folder):
        return False, "That model is not installed."
    try:
        shutil.rmtree(folder)
    except Exception as exc:
        return False, f"Could not remove {name}: {exc}"
    return True, ""


def import_live2d_folder(src: str) -> Tuple[bool, str]:
    """Copy a model folder the user already owns into the avatar root.

    This is the path for models the catalogue cannot legally offer - a VTuber
    model its creator licensed to the user, for example. The folder is copied
    as-is and must contain a `.model3.json` plus a non-empty `.moc3`.
    """
    src = str(src or "")
    if not os.path.isdir(src):
        return False, "Pick a folder that contains the model."
    model_file = _find_live2d_model_file(src)
    if not model_file:
        return False, "That folder has no *.model3.json in it."
    try:
        with open(model_file, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
        refs = data.get("FileReferences") or {}
        moc = str(refs.get("Moc") or "")
    except Exception as exc:
        return False, f"Could not read {os.path.basename(model_file)}: {exc}"
    if not moc:
        return False, "The model file does not reference a .moc3."
    moc_path = os.path.join(os.path.dirname(model_file), moc)
    if not os.path.isfile(moc_path) or os.path.getsize(moc_path) == 0:
        return False, f"The .moc3 is missing or empty ({moc})."

    _ensure(live2d_dir())
    slug = _slugify(os.path.basename(src.rstrip("\\/")), "live2d-model")
    dest = _unique_dir(live2d_dir(), slug)
    try:
        shutil.copytree(src, dest)
    except Exception as exc:
        shutil.rmtree(dest, ignore_errors=True)
        return False, f"Could not copy the folder: {exc}"
    _write_json(os.path.join(dest, "PROVENANCE.json"), {
        "kind": "live2d",
        "name": os.path.basename(model_file)[:-len(".model3.json")],
        "source": "imported",
        "original_path": os.path.abspath(src),
        "licence": "user-supplied - not verified",
        "installed_at": _stamp(),
    })
    return True, ""


# ---------------------------------------------------------------------------
# VRM catalogue
# ---------------------------------------------------------------------------
# Every entry below was opened and its *own* metadata read, not its README.
# `verdict` is what the file declares; `install_vrm` re-reads the downloaded
# file, so this table is a claim that the download is checked against.
VRM_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "cryptovoxels",
        "name": "Cryptovoxels Avatar",
        "url": "https://raw.githubusercontent.com/madjin/vrm-samples/master/cryptovoxels.vrm",
        "bytes": 238284,
        "sha256": "c5b3ae99adb04138f757b26e836c9e7d763377e7dd7b0c646c2515e818e0cad3",
        "spec": "VRM 0.x",
        "verdict": "permissive",
        "licence": "VRoid Hub: redistribution allowed, modification allowed, "
                   "commercial use allowed, attribution not required",
        "licence_url": "https://hub.vroid.com/license",
        "author": "Cryptovoxels",
        "note": "Small, low-poly, very fast to load. A good first test.",
    },
    {
        "id": "seed-san",
        "name": "Seed-san",
        "url": "https://raw.githubusercontent.com/madjin/vrm-samples/master/"
               "Seed-san/vrm/Seed-san.vrm",
        "bytes": 10745680,
        "sha256": "d24a5473a53f5ab76228da7c49a3f6fc2e1a0561566123859311df1e40b26490",
        "spec": "VRM 1.0",
        "verdict": "permissive",
        "licence": "VRM Public License 1.0: redistribution allowed, commercial use "
                   "allowed (corporation), attribution required",
        "licence_url": "https://vrm.dev/licenses/1.0/",
        "author": "VirtualCast, Inc.",
        "note": "Full body, facial expressions and spring bones.",
    },
    {
        "id": "twist-sample",
        "name": "VRM Twist Sample",
        "url": "https://raw.githubusercontent.com/pixiv/three-vrm/dev/packages/"
               "three-vrm/examples/models/VRM1_Constraint_Twist_Sample.vrm",
        "bytes": 10776032,
        "sha256": "12c2b97e95e700783a6a550dc0eee2d7880aeedccef9ae67bc4c5a2f0f2631a2",
        "spec": "VRM 1.0",
        "verdict": "permissive",
        "licence": "VRM Public License 1.0: redistribution allowed, commercial use "
                   "allowed (corporation)",
        "licence_url": "https://vrm.dev/licenses/1.0/",
        "author": "pixiv Inc.",
        "note": "pixiv's rigging sample. Simple, but a clean VRM 1.0 reference.",
    },
]


def vrm_catalog() -> List[Dict[str, Any]]:
    return [dict(entry) for entry in VRM_CATALOG]


def _permissive(verdict: str) -> bool:
    low = str(verdict or "").lower()
    return low.startswith(("permissive", "cc0", "cc by"))


def _vrm_verdict(path: str) -> Dict[str, Any]:
    """Read the downloaded file's declaration, using the same reader the app uses."""
    try:
        from core import avatar_runtime

        info = avatar_runtime.scan_vrm_model(path)
        return info if isinstance(info, dict) else {}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "verdict": ""}


def install_vrm(
    entry_or_id: Any,
    on_progress: ProgressFn = None,
    cancel: CancelFn = None,
) -> Tuple[bool, str]:
    """Download a VRM, verify its pinned digest, then re-check its licence.

    The order matters: the digest proves the bytes are the ones that were
    checked when the catalogue was written, and the licence read proves the
    file still declares what was claimed. Either check failing removes the
    download rather than leaving a model the UI cannot justify offering.
    """
    entry = _find_vrm(entry_or_id)
    if not entry:
        return False, "That VRM is not in the catalogue."
    _ensure(vrm_dir())
    dest = os.path.join(vrm_dir(), f"{entry['id']}.vrm")
    if os.path.isfile(dest):
        return False, f"{entry['name']} is already installed."

    ok, err = file_download.download_file(
        str(entry.get("url") or ""),
        dest,
        expected_size=int(entry.get("bytes") or 0),
        expected_sha256=str(entry.get("sha256") or ""),
        on_progress=on_progress,
        cancel=cancel,
        timeout=120.0,
        user_agent=USER_AGENT,
    )
    if not ok:
        return False, err

    info = _vrm_verdict(dest)
    if not info.get("ok"):
        file_download.discard(dest)
        return False, (
            f"{entry['name']} downloaded but could not be read as VRM: "
            f"{info.get('error') or 'unknown error'}"
        )
    if not _permissive(str(info.get("verdict") or "")):
        file_download.discard(dest)
        return False, (
            f"{entry['name']} declares '{info.get('verdict')}', which is not a "
            "redistribution grant, so it was not installed."
        )

    _write_json(os.path.join(vrm_dir(), f"{entry['id']}.PROVENANCE.json"), {
        "kind": "vrm",
        "name": entry["name"],
        "source": entry["url"],
        "licence": entry["licence"],
        "licence_url": entry["licence_url"],
        "author": entry["author"],
        "verdict": info.get("verdict"),
        "spec": info.get("spec"),
        "sha256": entry["sha256"],
        "installed_at": _stamp(),
    })
    try:
        with open(os.path.join(vrm_dir(), f"{entry['id']}.LICENSE.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write(
                f"{entry['name']}\n{'=' * len(entry['name'])}\n\n"
                f"Author : {entry['author']}\n"
                f"Licence: {entry['licence']}\n"
                f"Details: {entry['licence_url']}\n"
                f"File   : {entry['url']}\n"
                f"SHA-256: {entry['sha256']}\n\n"
                f"Declared by the file itself: {info.get('verdict')} "
                f"(spec {info.get('spec')})\n"
                f"Installed {_stamp()} by Brahma Evo.\n"
            )
    except Exception:
        pass
    return True, ""


def _find_vrm(entry_or_id: Any) -> Optional[Dict[str, Any]]:
    if isinstance(entry_or_id, dict):
        return entry_or_id
    wanted = str(entry_or_id or "")
    for entry in VRM_CATALOG:
        if entry["id"] == wanted:
            return entry
    return None


def installed_vrm() -> List[Dict[str, Any]]:
    """Every .vrm in the avatar root, with its own declared licence verdict."""
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(vrm_dir()):
        return out
    for name in sorted(os.listdir(vrm_dir())):
        if not name.lower().endswith(".vrm"):
            continue
        path = os.path.join(vrm_dir(), name)
        if not os.path.isfile(path):
            continue
        info = _vrm_verdict(path)
        provenance = {}
        try:
            with open(os.path.join(vrm_dir(), name[:-4] + ".PROVENANCE.json"),
                      "r", encoding="utf-8") as fh:
                provenance = json.load(fh)
        except Exception:
            pass
        out.append({
            "id": name[:-4],
            "name": info.get("name") or name[:-4],
            "path": path,
            "bytes": int(info.get("bytes") or 0) or os.path.getsize(path),
            "spec": info.get("spec", ""),
            "verdict": info.get("verdict", ""),
            "ok": bool(info.get("ok")),
            "error": info.get("error", ""),
            "provenance": provenance if isinstance(provenance, dict) else {},
        })
    return out


def delete_vrm(name_or_path: str) -> Tuple[bool, str]:
    path = str(name_or_path or "")
    if not path.lower().endswith(".vrm"):
        path = os.path.join(vrm_dir(), f"{os.path.basename(path)}.vrm")
    if not os.path.isfile(path):
        return False, "That VRM is not installed."
    try:
        os.remove(path)
    except Exception as exc:
        return False, f"Could not remove the VRM: {exc}"
    for suffix in (".PROVENANCE.json", ".LICENSE.txt"):
        try:
            os.remove(path[:-4] + suffix)
        except Exception:
            pass
    return True, ""


def import_vrm(src: str) -> Tuple[bool, str]:
    """Copy a .vrm the user owns into the avatar root.

    Unlike the catalogue, this does not require a redistribution grant: a file
    the user owns may be restricted to private use, which is exactly what a
    desktop avatar is. The verdict is recorded and surfaced so nobody mistakes
    "it runs here" for "it may be shared".
    """
    src = str(src or "")
    if not os.path.isfile(src) or not src.lower().endswith(".vrm"):
        return False, "Pick a .vrm file."
    info = _vrm_verdict(src)
    if not info.get("ok"):
        return False, f"That file is not a readable VRM: {info.get('error') or 'unknown error'}"
    _ensure(vrm_dir())
    slug = _slugify(os.path.splitext(os.path.basename(src))[0], "avatar")
    dest = os.path.join(vrm_dir(), f"{slug}.vrm")
    if os.path.exists(dest):
        base, _ext = os.path.splitext(dest)
        for i in range(2, 1000):
            candidate = f"{base}-{i}.vrm"
            if not os.path.exists(candidate):
                dest = candidate
                break
    try:
        shutil.copyfile(src, dest)
    except Exception as exc:
        return False, f"Could not copy the file: {exc}"
    _write_json(dest[:-4] + ".PROVENANCE.json", {
        "kind": "vrm",
        "name": info.get("name") or os.path.basename(src),
        "source": "imported",
        "original_path": os.path.abspath(src),
        "licence": "user-supplied - not verified for redistribution",
        "verdict": info.get("verdict"),
        "spec": info.get("spec"),
        "installed_at": _stamp(),
    })
    return True, ""


# ---------------------------------------------------------------------------
# Backgrounds: scanning and validation
# ---------------------------------------------------------------------------
SCENE_EXTS = (".glb", ".gltf")
PANORAMA_EXTS = (".hdr", ".jpg", ".jpeg", ".png")
BACKGROUND_EXTS = SCENE_EXTS + PANORAMA_EXTS
_MAX_BACKGROUND_BYTES = 512 * 1024 * 1024  # WebGL dies on anything near this


def _read_glb_json(path: str) -> Tuple[Dict[str, Any], str]:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(12)
            if len(head) < 12 or struct.unpack("<I", head[:4])[0] != 0x46546C67:
                return {}, "not a GLB container"
            magic_ver, total_len = struct.unpack("<II", head[4:12])
            if magic_ver != 2:
                return {}, f"GLB version {magic_ver} is not supported (need 2)"
            if total_len > size:
                return {}, f"GLB header claims {total_len} bytes but the file has {size} (truncated)"
            chunk_len, chunk_type = struct.unpack("<II", fh.read(8))
            if chunk_type != 0x4E4F534A:
                return {}, "first GLB chunk is not JSON"
            data = json.loads(fh.read(chunk_len).decode("utf-8", errors="replace"))
        return (data if isinstance(data, dict) else {}), ""
    except Exception as exc:
        return {}, f"could not parse the GLB: {exc}"


def _png_size(path: str) -> Tuple[int, int]:
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return 0, 0
        width, height = struct.unpack(">II", head[16:24])
        return int(width), int(height)
    except Exception:
        return 0, 0


def _jpeg_size(path: str) -> Tuple[int, int]:
    try:
        with open(path, "rb") as fh:
            if fh.read(2) != b"\xff\xd8":
                return 0, 0
            while True:
                marker = fh.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    return 0, 0
                code = marker[1]
                if code in (0xD8, 0xD9):
                    continue
                length_raw = fh.read(2)
                if len(length_raw) < 2:
                    return 0, 0
                length = struct.unpack(">H", length_raw)[0]
                if code in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    data = fh.read(5)
                    if len(data) < 5:
                        return 0, 0
                    height, width = struct.unpack(">HH", data[1:5])
                    return int(width), int(height)
                fh.seek(length - 2, os.SEEK_CUR)
    except Exception:
        return 0, 0


def _gltf_local_uris(path: str) -> Tuple[List[str], str]:
    """Relative buffer/image URIs a .gltf needs beside it. Data URIs are skipped."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except Exception as exc:
        return [], f"could not parse the .gltf: {exc}"
    if not isinstance(data, dict):
        return [], "the .gltf is not a JSON object"
    uris: List[str] = []
    for group in ("buffers", "images"):
        for item in data.get(group) or []:
            if not isinstance(item, dict):
                continue
            uri = str(item.get("uri") or "")
            if uri and not uri.lower().startswith("data:"):
                uris.append(uri)
    return uris, ""


def scan_background(folder: str) -> Dict[str, Any]:
    """Validate one background folder and pick the file the page should load.

    Preference order is fixed rather than "whatever is newest": a folder with
    both a scene and a skybox renders the scene, so an import that adds a stray
    jpeg cannot silently change what the user sees.
    """
    out: Dict[str, Any] = {
        "folder": folder or "", "name": os.path.basename(str(folder or "").rstrip("\\/")),
        "kind": "", "file": "", "bytes": 0, "total_bytes": 0,
        "ok": False, "error": "", "warnings": [], "provenance": {},
    }
    if not folder or not os.path.isdir(folder):
        out["error"] = "background folder not found"
        return out

    out["total_bytes"] = _dir_bytes(folder)
    try:
        with open(os.path.join(folder, "PROVENANCE.json"), "r", encoding="utf-8") as fh:
            prov = json.load(fh)
        out["provenance"] = prov if isinstance(prov, dict) else {}
    except Exception:
        pass
    if isinstance(out["provenance"].get("name"), str) and out["provenance"]["name"]:
        out["name"] = out["provenance"]["name"]

    found: List[str] = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.lower().endswith(BACKGROUND_EXTS) and not f.endswith(".part"):
                found.append(os.path.join(root, f))

    def rank(path: str) -> int:
        base = os.path.basename(path).lower()
        ext = os.path.splitext(base)[1]
        if base in ("scene.glb", "scene.gltf"):
            return 0
        if ext == ".glb":
            return 1
        if ext == ".gltf":
            return 2
        if base == "panorama.hdr":
            return 3
        if ext == ".hdr":
            return 4
        if base.startswith("panorama"):
            return 5
        return 6

    if not found:
        out["error"] = "no .glb, .gltf, .hdr, .jpg or .png in the folder"
        return out

    chosen = sorted(found, key=rank)[0]
    ext = os.path.splitext(chosen)[1].lower()
    out["file"] = chosen
    out["bytes"] = os.path.getsize(chosen)

    if out["bytes"] > _MAX_BACKGROUND_BYTES:
        out["error"] = (
            f"{out['bytes'] / 1048576:.0f} MB is larger than the "
            f"{_MAX_BACKGROUND_BYTES // 1048576} MB limit for a background"
        )
        return out

    if ext == ".glb":
        out["kind"] = "glb"
        data, err = _read_glb_json(chosen)
        if err:
            out["error"] = err
            return out
        if not (data.get("scenes") or data.get("nodes") or data.get("meshes")):
            out["error"] = "the GLB has no scenes, nodes or meshes to render"
            return out
    elif ext == ".gltf":
        out["kind"] = "gltf"
        uris, err = _gltf_local_uris(chosen)
        if err:
            out["error"] = err
            return out
        missing = [u for u in uris
                   if not os.path.isfile(os.path.join(os.path.dirname(chosen), u))]
        if missing:
            out["warnings"].append(
                "missing beside the .gltf: " + ", ".join(missing[:4])
            )
    elif ext == ".hdr":
        out["kind"] = "panorama"
        try:
            with open(chosen, "rb") as fh:
                head = fh.read(32)
            if not (head.startswith(b"#?RADIANCE") or head.startswith(b"#?RGBE")):
                out["error"] = "not a Radiance .hdr file"
                return out
        except Exception as exc:
            out["error"] = f"could not read the .hdr: {exc}"
            return out
    else:
        out["kind"] = "panorama"
        width, height = _png_size(chosen) if ext == ".png" else _jpeg_size(chosen)
        if not width or not height:
            out["error"] = f"could not read the image header ({ext})"
            return out
        if not (1.8 <= (width / height) <= 2.2):
            out["warnings"].append(
                f"image is {width}x{height}, not a 2:1 equirectangular panorama"
            )

    out["ok"] = True
    return out


def list_backgrounds() -> List[Dict[str, Any]]:
    """Every background folder that validates, sorted by display name."""
    out: List[Dict[str, Any]] = []
    root = backgrounds_dir()
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        if name.startswith("_") or name.startswith(".") or name.endswith(".installing"):
            continue
        folder = os.path.join(root, name)
        if not os.path.isdir(folder):
            continue
        info = scan_background(folder)
        if info["ok"]:
            out.append(info)
    out.sort(key=lambda e: str(e["name"]).lower())
    return out


def _copy_with_progress(src: str, dest: str, on_progress: ProgressFn,
                        cancel: CancelFn, label: str, base: int, total: int) -> Tuple[bool, str]:
    try:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        done = 0
        with open(src, "rb") as fin, open(dest, "wb") as fout:
            while True:
                if cancel is not None and cancel():
                    return False, "Cancelled."
                chunk = fin.read(1024 * 512)
                if not chunk:
                    break
                fout.write(chunk)
                done += len(chunk)
                if on_progress is not None and total:
                    try:
                        on_progress(base + done, total, label)
                    except Exception:
                        pass
        try:
            shutil.copystat(src, dest)
        except Exception:
            pass
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _import_background_file(src: str, on_progress: ProgressFn,
                            cancel: CancelFn) -> Tuple[bool, str]:
    ext = os.path.splitext(src)[1].lower()
    _ensure(backgrounds_dir())
    slug = _slugify(os.path.splitext(os.path.basename(src))[0], "background")
    dest = _unique_dir(backgrounds_dir(), slug)
    try:
        os.makedirs(dest)
    except Exception as exc:
        return False, f"Could not create the background folder: {exc}"

    files: List[Tuple[str, str]] = [(src, os.path.basename(src))]
    if ext == ".gltf":
        uris, err = _gltf_local_uris(src)
        if err:
            shutil.rmtree(dest, ignore_errors=True)
            return False, err
        for uri in uris:
            if uri.startswith(("/", "\\")) or ".." in uri.split("/"):
                continue  # never follow a gltf outside its own folder
            candidate = os.path.join(os.path.dirname(src), uri.replace("/", os.sep))
            if os.path.isfile(candidate):
                files.append((candidate, uri.replace("/", os.sep)))

    total = sum(os.path.getsize(p) for p, _ in files)
    done = 0
    for source, relative in files:
        ok, err = _copy_with_progress(
            source, os.path.join(dest, relative), on_progress, cancel,
            os.path.basename(relative), done, total,
        )
        if not ok:
            if err == "Cancelled.":
                shutil.rmtree(dest, ignore_errors=True)
            return False, err
        done += os.path.getsize(source)

    info = scan_background(dest)
    if not info["ok"]:
        shutil.rmtree(dest, ignore_errors=True)
        return False, f"That file cannot be used as a background: {info['error']}"
    _write_json(os.path.join(dest, "PROVENANCE.json"), {
        "kind": "background",
        "name": os.path.splitext(os.path.basename(src))[0],
        "source": "imported",
        "original_path": os.path.abspath(src),
        "asset_kind": info["kind"],
        "licence": "user-supplied - not verified",
        "imported_at": _stamp(),
    })
    return True, ""


def _import_background_dir(src: str, on_progress: ProgressFn,
                           cancel: CancelFn) -> Tuple[bool, str]:
    info = scan_background(src)
    if not info["ok"]:
        return False, f"That folder cannot be used as a background: {info['error']}"
    _ensure(backgrounds_dir())
    slug = _slugify(os.path.basename(src.rstrip("\\/")), "background")
    dest = _unique_dir(backgrounds_dir(), slug)

    pairs: List[Tuple[str, str]] = []
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs
                   if not d.startswith(".") and d not in ("__pycache__", "node_modules")]
        for f in files:
            if f in (".DS_Store", "Thumbs.db") or f.endswith(".part"):
                continue
            source = os.path.join(root, f)
            relative = os.path.relpath(source, src)
            pairs.append((source, relative))
    total = sum(os.path.getsize(p) for p, _ in pairs)
    done = 0
    try:
        os.makedirs(dest)
    except Exception as exc:
        return False, f"Could not create the background folder: {exc}"
    for source, relative in pairs:
        ok, err = _copy_with_progress(
            source, os.path.join(dest, relative), on_progress, cancel,
            os.path.basename(relative), done, total,
        )
        if not ok:
            if err == "Cancelled.":
                shutil.rmtree(dest, ignore_errors=True)
            return False, err
        done += os.path.getsize(source)

    copied = scan_background(dest)
    if not copied["ok"]:
        shutil.rmtree(dest, ignore_errors=True)
        return False, f"The copied folder is not usable: {copied['error']}"
    _write_json(os.path.join(dest, "PROVENANCE.json"), {
        "kind": "background",
        "name": os.path.basename(src.rstrip("\\/")),
        "source": "imported",
        "original_path": os.path.abspath(src),
        "asset_kind": copied["kind"],
        "licence": "user-supplied - not verified",
        "imported_at": _stamp(),
    })
    return True, ""


def import_background(src: str, on_progress: ProgressFn = None,
                      cancel: CancelFn = None) -> Tuple[bool, str]:
    """Copy a file or folder the user picked into the backgrounds root."""
    src = str(src or "")
    if os.path.isdir(src):
        return _import_background_dir(src, on_progress, cancel)
    if os.path.isfile(src) and src.lower().endswith(BACKGROUND_EXTS):
        return _import_background_file(src, on_progress, cancel)
    return False, "Pick a .glb, .gltf, .hdr, .jpg or .png file, or a folder with one."


def delete_background(name: str) -> Tuple[bool, str]:
    safe = os.path.basename(str(name or "").rstrip("\\/"))
    if not safe or safe.startswith("_"):
        return False, "That background cannot be removed."
    folder = os.path.join(backgrounds_dir(), safe)
    root = os.path.abspath(backgrounds_dir())
    if os.path.commonpath([root, os.path.abspath(folder)]) != root:
        return False, "That path is outside the backgrounds folder."
    if not os.path.isdir(folder):
        return False, "That background is not installed."
    try:
        shutil.rmtree(folder)
    except Exception as exc:
        return False, f"Could not remove {safe}: {exc}"
    return True, ""


# ---------------------------------------------------------------------------
# Backgrounds: Poly Haven CC0 HDRI catalogue
# ---------------------------------------------------------------------------
POLYHAVEN_ASSETS_URL = "https://api.polyhaven.com/assets?t=hdris"
POLYHAVEN_FILES_URL = "https://api.polyhaven.com/files/"
POLYHAVEN_LICENSE_URL = "https://polyhaven.com/license"
HDRI_RESOLUTIONS = ("1k", "2k", "4k")


def _polyhaven_assets() -> Dict[str, Any]:
    data = _cached_json(POLYHAVEN_ASSETS_URL, "polyhaven-assets.json")
    return data if isinstance(data, dict) else {}


def polyhaven_catalog(force: bool = False) -> List[Dict[str, Any]]:
    """Searchable HDRI list. Sizes are filled in only when an asset is chosen.

    Asking the files endpoint for all 997 assets up front would be 997
    requests for data the list itself can show without; the download step is
    where the exact URL, size and md5 are needed.
    """
    if force:
        try:
            os.remove(os.path.join(_cache_dir(), "polyhaven-assets.json"))
        except Exception:
            pass
    out: List[Dict[str, Any]] = []
    for asset_id, info in _polyhaven_assets().items():
        if not isinstance(info, dict):
            continue
        categories = [str(c) for c in (info.get("categories") or []) if c]
        tags = [str(t) for t in (info.get("tags") or []) if t]
        out.append({
            "engine": "polyhaven",
            "id": str(asset_id),
            "name": str(info.get("name") or asset_id),
            "categories": categories,
            "tags": tags,
            "category": str(info.get("category") or ""),
            "description": str(info.get("description") or ""),
            "thumbnail": f"https://cdn.polyhaven.com/asset_img/thumbs/{asset_id}.png?width=320&height=160",
        })
    out.sort(key=lambda e: e["name"].lower())
    return out


def polyhaven_files(asset_id: str) -> Dict[str, Any]:
    asset_id = _slugify(asset_id, "")
    if not asset_id:
        return {}
    data = _cached_json(
        POLYHAVEN_FILES_URL + asset_id,
        f"polyhaven-files-{asset_id}.json",
        ttl=30 * 24 * 60 * 60,
    )
    return data if isinstance(data, dict) else {}


def polyhaven_download_info(asset_id: str, resolution: str = "2k") -> Dict[str, Any]:
    """Exact URL, size and md5 for one HDRI at one resolution, or {}."""
    if resolution not in HDRI_RESOLUTIONS:
        return {}
    files = polyhaven_files(asset_id)
    hdri = files.get("hdri")
    if not isinstance(hdri, dict):
        return {}
    node = hdri.get(resolution)
    if not isinstance(node, dict):
        return {}
    hdr = node.get("hdr")
    if not isinstance(hdr, dict) or not hdr.get("url"):
        return {}
    return {
        "url": str(hdr["url"]),
        "bytes": int(hdr.get("size") or 0),
        "md5": str(hdr.get("md5") or ""),
        "resolution": resolution,
    }


def download_polyhaven(
    asset_id: str,
    resolution: str = "2k",
    on_progress: ProgressFn = None,
    cancel: CancelFn = None,
) -> Tuple[bool, str]:
    """Download one CC0 HDRI as `<backgrounds>/<id>/panorama.hdr`.

    Verified against Poly Haven's own md5 and size, so the file is known to be
    the one their API described. The staged folder is renamed into place only
    after the check passes.
    """
    asset_id = _slugify(asset_id, "")
    if not asset_id:
        return False, "Unknown Poly Haven asset."
    dest = os.path.join(backgrounds_dir(), asset_id)
    if os.path.isdir(dest):
        return False, "That HDRI is already in your backgrounds."
    info = polyhaven_download_info(asset_id, resolution)
    if not info:
        return False, (
            f"Poly Haven has no {resolution} .hdr for '{asset_id}'. "
            "Try another resolution."
        )
    tmp = dest + ".installing"
    _ensure(backgrounds_dir())
    ok, err = file_download.download_file(
        info["url"],
        os.path.join(tmp, "panorama.hdr"),
        expected_size=info["bytes"],
        expected_md5=info["md5"],
        on_progress=on_progress,
        cancel=cancel,
        timeout=120.0,
        user_agent=USER_AGENT,
    )
    if not ok:
        if err == "Cancelled.":
            shutil.rmtree(tmp, ignore_errors=True)
        return False, err

    entry = _polyhaven_entry(asset_id)
    _write_json(os.path.join(tmp, "PROVENANCE.json"), {
        "kind": "background",
        "name": entry.get("name") or asset_id,
        "source": info["url"],
        "asset_kind": "panorama",
        "licence": "CC0 (Poly Haven)",
        "licence_url": POLYHAVEN_LICENSE_URL,
        "resolution": resolution,
        "md5": info["md5"],
        "bytes": info["bytes"],
        "imported_at": _stamp(),
    })
    try:
        with open(os.path.join(tmp, "LICENSE.txt"), "w", encoding="utf-8") as fh:
            fh.write(
                f"{entry.get('name') or asset_id}\n"
                f"Source : {info['url']}\n"
                f"Licence: CC0 - {POLYHAVEN_LICENSE_URL}\n"
                f"Resolution: {resolution} ({info['bytes'] / 1048576:.1f} MB)\n"
                f"MD5    : {info['md5']}\n\n"
                "CC0 waives copyright, so this file may be used, modified and\n"
                "redistributed without attribution. Credit to Poly Haven is\n"
                "appreciated but not required.\n"
                f"Downloaded {_stamp()} by Brahma Evo.\n"
            )
    except Exception:
        pass
    try:
        os.replace(tmp, dest)
    except Exception as exc:
        return False, f"Could not finalise {asset_id}: {exc}"
    return True, ""


def _polyhaven_entry(asset_id: str) -> Dict[str, Any]:
    assets = _polyhaven_assets()
    info = assets.get(asset_id)
    if isinstance(info, dict):
        return {"name": str(info.get("name") or asset_id)}
    return {"name": asset_id}
