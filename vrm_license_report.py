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
        print(f"  licenceUrl  : {r['licenseUrl']}")
        if r["otherLicenseUrl"]:
            print(f"  otherLicence: {r['otherLicenseUrl']}")
        print(f"  VERDICT     : {r['verdict'].upper()} - {r['detail']}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))