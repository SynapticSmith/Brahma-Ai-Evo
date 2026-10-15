"""
vrm_license_report.py

Read the licence a .vrm file declares *about itself*.

Why this exists
---------------
VRM carries its own permission metadata inside the file, which is unusual for
3D assets and unusually useful: the licence travels with the model rather than
living in a README that can drift away from the binary. So "check the licence"
does not have to mean "check what a README says" - it can mean "ask the file".

Two spec versions, two shapes:

    VRM 1.0   extensions.VRMC_vrm.meta
    VRM 0.x   asset.extras.VRM.meta   (written by UniGLTF)

Both are checked, and a file that declares nothing is reported as declaring
nothing rather than being quietly treated as permissive.

Two kinds of declaration are read. The CC family and the old VRoid Hub
"conditions of use" URLs are matched by token. The "VRM Public License 1.0"
(https://vrm.dev/licenses/1.0/) is different again: it is a template whose
License Settings are written into the file as meta flags
(`allowRedistribution`, `modification`, `commercialUsage`, `avatarPermission`),
so it is judged from those flags - not from the licence name, which on its own
permits everything from "no restrictions" to "no redistribution".

Usage: py -3.11 vrm_license_report.py <file.vrm> [more.vrm ...]
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

PERMISSIVE = {
    "cc0": ("CC0", "public domain dedication - copyright waived"),
    "cc-by-4.0": ("CC BY 4.0", "free use and redistribution with attribution"),
    "cc-by-3.0": ("CC BY 3.0", "free use and redistribution with attribution"),
    "cc-by-nc-4.0": ("CC BY-NC 4.0", "free, NON-COMMERCIAL only"),
    "cc-by-nc-3.0": ("CC BY-NC 3.0", "free, NON-COMMERCIAL only"),
    "cc-by-sa-4.0": ("CC BY-SA 4.0", "free with attribution and share-alike"),
}

NON_PERMISSIVE = {
    "only-redistribution-and-use-conditions-for-characters": (
        "custom", "redistribution/use conditions apply - read the linked terms"),
    "redistribution prohibited": ("prohibited", "may not be redistributed"),
}

# "VRM Public License 1.0" is not one licence but a template: the author picks
# License Settings (relaxed / standard / custom) and those choices are written
# INTO the file as meta flags. So the URL alone cannot decide the verdict - the
# flags can. The canonical url is https://vrm.dev/licenses/1.0/.
VRM_PUBLIC_LICENSE_TOKENS = ("vrm.dev/licenses", "vrm public license")

# The meta keys that carry a VRM 1.0 file's License Settings. Their presence is
# what makes the file self-describing enough to judge.
_VRM1_LICENSE_SETTINGS = ("allowRedistribution", "modification",
                          "commercialUsage", "avatarPermission")


def read_gltf_json(path: Path) -> dict:
    """The JSON chunk of a .vrm (which is a GLB)."""
    with open(path, "rb") as fh:
        header = fh.read(12)
        if len(header) < 12:
            raise ValueError("file is too small to be a GLB")
        magic, _version, _length = struct.unpack("<III", header)
        if magic != 0x46546C67:
            raise ValueError("not a GLB/glTF binary")
        chunk_len, chunk_type = struct.unpack("<II", fh.read(8))
        if chunk_type != 0x4E4F534A:      # b'JSON'
            raise ValueError("first GLB chunk is not JSON")
        return json.loads(fh.read(chunk_len).decode("utf-8"))


def extract_meta(data: dict) -> tuple:
    """(spec_version, meta_dict) for either VRM spec, or ("", {})."""
    exts = data.get("extensions") or {}
    vrm1 = exts.get("VRMC_vrm")
    if isinstance(vrm1, dict):
        return str(vrm1.get("specVersion", "1.0")), (vrm1.get("meta") or {})
    vrm0 = (exts.get("VRM")) or ((data.get("asset") or {}).get("extras", {})
                                 .get("VRM")) or {}
    if isinstance(vrm0, dict) and isinstance(vrm0.get("meta"), dict):
        return "0.x", vrm0["meta"]
    return "", {}


def _vroid_hub_flags(url: str) -> dict:
    """VRoid Hub expresses permissions as query flags, not a licence name."""
    out = {}
    for pair in url.split("?", 1)[-1].split("&"):
        if "=" in pair:
            k, _, v = pair.partition("=")
            out[k] = v
    return out


def vrm_public_license(meta: dict, url: str) -> tuple:
    """(verdict, human) for a VRM Public License 1.0 file, from its own flags.

    The licence name is a template, so judging it by name would be guessing in
    both directions. What the file declares concretely - whether it may be
    redistributed, whether commercial use is allowed, whether the face may be
    animated by others - is in the meta. That is the evidence used here.

    A file that cites the url but carries no License Settings (e.g. a VRM 0.x
    model that merely names the licence) cannot be verified and is reported as
    unknown rather than waved through.
    """
    if not any(k in meta for k in _VRM1_LICENSE_SETTINGS):
        return "unknown", ("VRM Public License url, but no in-file License "
                           f"Settings to judge: {url}")

    redistribution = meta.get("allowRedistribution")
    modification = str(meta.get("modification") or "")
    permission = str(meta.get("avatarPermission") or "")
    commercial = str(meta.get("commercialUsage") or "")
    credit = str(meta.get("creditNotation") or "")

    if permission == "onlyAuthor":
        return "restricted", ("VRM Public License 1.0: avatar use limited to "
                              f"the author ({url})")

    can_redistribute = (redistribution is True
                        or modification == "allowModificationRedistribution")
    if not can_redistribute:
        return "restricted", (f"VRM Public License 1.0: redistribution not "
                              f"granted (allowRedistribution={redistribution!r}, "
                              f"modification={modification!r}) ({url})")

    if commercial in ("personalProfit", "corporation"):
        bits = [f"commercial use allowed ({commercial})"]
    elif commercial == "personalNonProfit":
        bits = ["NON-COMMERCIAL only"]
    else:
        bits = [f"commercial usage {commercial!r}"]
    if credit == "required":
        bits.append("attribution required")
    return "permissive", ("VRM Public License 1.0: redistribution allowed; "
                          + "; ".join(bits) + f" ({url})")


def classify(meta: dict) -> tuple:
    """(verdict, human) from a VRM meta block. Never guesses.

    Two places a model can declare its licence, and both are checked:
    `licenseUrl` (the canonical field) and `otherLicenseUrl` (what VRoid Hub
    writes). A model that only fills the latter is *declared*, not undeclared -
    treating it as undeclared would discard the strongest evidence available.
    """
    url = str(meta.get("licenseUrl") or "").strip()
    other = str(meta.get("otherLicenseUrl") or "").strip()

    if url:
        low = url.lower()
        if any(tok in low for tok in VRM_PUBLIC_LICENSE_TOKENS):
            return vrm_public_license(meta, url)
        for token, human in PERMISSIVE.items():
            if token in low:
                return "permissive", f"{human} ({url})"
        for token, human in NON_PERMISSIVE.items():
            if token in low:
                return "restricted", f"{human} ({url})"
        return "unknown", f"licence url present but not recognised: {url}"

    if other:
        flags = _vroid_hub_flags(other)
        if flags:
            allow = ("redistribution", "modification")
            if all(flags.get(k) == "allow" for k in allow):
                bits = []
                if flags.get("corporate_commercial_use") == "allow":
                    bits.append("corporate commercial use allowed")
                if flags.get("personal_commercial_use") == "profit":
                    bits.append("personal commercial use allowed")
                if flags.get("credit") == "unnecessary":
                    bits.append("attribution not required")
                summary = "; ".join(bits) or "redistribution and modification allowed"
                return "permissive", f"VRoid Hub flags: {summary} ({other})"
            blocked = [k for k in allow if flags.get(k) == "prohibited"]
            if blocked:
                return "restricted", (f"VRoid Hub prohibits: {', '.join(blocked)} "
                                     f"({other})")
        return "unknown", f"only an otherLicenseUrl, not recognised: {other}"

    return "undeclared", "the file declares no licenceUrl"


def report(path: Path) -> dict:
    data = read_gltf_json(path)
    spec, meta = extract_meta(data)
    verdict, human = classify(meta)
    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "spec": spec or "(none detected)",
        "name": str(meta.get("name") or ""),
        "authors": meta.get("authors"),
        "copyright": meta.get("copyrightInformation"),
        "commercial": meta.get("commercialUsage"),
        "modification": meta.get("modification"),
        "redistribution": meta.get("allowRedistribution"),
        "avatarPermission": meta.get("avatarPermission"),
        "creditNotation": meta.get("creditNotation"),
        "licenseUrl": meta.get("licenseUrl"),
        "otherLicenseUrl": meta.get("otherLicenseUrl"),
        "verdict": verdict,
        "detail": human,
    }


def main(argv) -> int:
    for arg in argv:
        path = Path(arg)
        try:
            r = report(path)
        except Exception as exc:
            print(f"{path.name}: ERROR {exc}")
            continue
        print(f"{r['file']}  ({r['bytes']} bytes, VRM {r['spec']})")
        print(f"  name        : {r['name']}")
        print(f"  authors     : {r['authors']}")
        print(f"  copyright   : {r['copyright']}")
        print(f"  commercial  : {r['commercial']}   modification: {r['modification']}")
        print(f"  settings    : redistribute={r['redistribution']}   "
              f"avatarPermission={r['avatarPermission']}   "
              f"credit={r['creditNotation']}")
        print(f"  licenceUrl  : {r['licenseUrl']}")
        if r["otherLicenseUrl"]:
            print(f"  otherLicence: {r['otherLicenseUrl']}")
        print(f"  VERDICT     : {r['verdict'].upper()} - {r['detail']}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))