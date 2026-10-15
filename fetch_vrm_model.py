"""
fetch_vrm_model.py

Install a VRM avatar whose licence is verified from the file itself.

Licence verification, from the binary rather than a README
----------------------------------------------------------
VRM is the opposite of Live2D here: the format carries its own permission
metadata *inside the file* (`licenseUrl`, or VRoid Hub's `otherLicenseUrl`
flags), plus `authors`, `copyrightInformation`, `commercialUsage` and
`modification`. So "check the licence" does not have to mean "check what a
README claims" - it can mean asking the file.

That matters, because the repos are not consistent. Checked on 2026-10-14:

    cryptovoxels.vrm (238 KB, VRM 0.x)
        otherLicenseUrl: redistribution=allow, modification=allow,
        corporate_commercial_use=allow, personal_commercial_use=profit,
        credit=unnecessary
        -> permissive. Installed.

    Avatar_Orion.vrm (6.1 MB)
        declares no licence at all. Rejected by default.

    Seed-san.vrm (10.7 MB, VRM 1.0)
        VirtualCast, Inc.; VRM Public License 1.0. The file's License Settings
        are permissive: allowRedistribution=true, commercialUsage=corporation,
        modification=allowModificationRedistribution, creditNotation=required.
        -> permissive. Installable as the VRM 1.0 sample.

    VRM1_Constraint_Twist_Sample.vrm (three-vrm, 10.7 MB, VRM 1.0)
        pixiv Inc. (c) 2022; same licence, also permissive in the file
        (allowRedistribution=true, commercialUsage=corporation).

The "VRM Public License 1.0" is a template, not one licence: the author picks
License Settings and they are written into the file, so the verdict is read from
those flags (`allowRedistribution`, `modification`, `commercialUsage`,
`avatarPermission`) rather than from the licence name.

This script therefore gates on the file's own declaration and refuses anything
it cannot read as permissive, unless --allow-restricted is passed. A repo being
MIT does not make the avatar inside it MIT, and this is where that bit people.

Each install writes its own record beside the model - `LICENSE.<model>.txt`,
`PROVENANCE.<name>.json`, `SOURCE-README.<name>.md` - so several VRM models can
live in the same folder without one install overwriting another's provenance.

Usage:  py -3.11 fetch_vrm_model.py [--model cryptovoxels|seed-san] [--force]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vrm_license_report import report as licence_report  # noqa: E402

SOURCES = {
    "cryptovoxels": {
        "url": "https://raw.githubusercontent.com/madjin/vrm-samples/master/"
               "cryptovoxels.vrm",
        "repo": "https://github.com/madjin/vrm-samples",
        "readme": "https://raw.githubusercontent.com/madjin/vrm-samples/"
                  "master/README.md",
        "note": "VRoid Hub licence flags embedded in the file; "
                "see LICENSE.cryptovoxels.txt",
    },
    "seed-san": {
        "url": "https://raw.githubusercontent.com/madjin/vrm-samples/master/"
               "Seed-san/vrm/Seed-san.vrm",
        "repo": "https://github.com/madjin/vrm-samples",
        "readme": "https://raw.githubusercontent.com/madjin/vrm-samples/"
                  "master/README.md",
        "note": "VRM 1.0. VirtualCast, Inc. VRM Public License 1.0 with "
                "permissive License Settings in the file (redistribution and "
                "corporation use allowed, attribution required); "
                "see LICENSE.seed-san.txt",
    },
    "orion": {
        "url": "https://raw.githubusercontent.com/madjin/vrm-samples/master/"
               "Avatar_Orion.vrm",
        "repo": "https://github.com/madjin/vrm-samples",
        "readme": "https://raw.githubusercontent.com/madjin/vrm-samples/"
                  "master/README.md",
        "note": "declares no licence - will be refused without --allow-restricted",
    },
    "twist": {
        "url": "https://raw.githubusercontent.com/pixiv/three-vrm/dev/packages/"
               "three-vrm/examples/models/VRM1_Constraint_Twist_Sample.vrm",
        "repo": "https://github.com/pixiv/three-vrm",
        "readme": "https://raw.githubusercontent.com/pixiv/three-vrm/dev/"
                  "README.md",
        "note": "VRM 1.0 constraint-twist sample, pixiv Inc. (c) 2022. VRM "
                "Public License 1.0 with permissive License Settings in the "
                "file; see LICENSE.twist.txt",
    },
}
DEFAULT_MODEL = "cryptovoxels"
USER_AGENT = "brahma-one-391-avatar-installer"


def _download(url: str, timeout: int = 300) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def default_root() -> Path:
    return (Path(os.environ.get("LOCALAPPDATA", Path.home())) /
            "BrahmaAI" / "avatar" / "vrm")


def install(name: str, dest_root: Path, force: bool = False,
            allow_restricted: bool = False) -> int:
    src = SOURCES.get(name)
    if not src:
        print(f"[fail] unknown model {name!r}; known: {', '.join(sorted(SOURCES))}")
        return 1

    dest = dest_root / f"{name}.vrm"
    if dest.exists() and not force:
        print(f"[skip] {dest} already installed (use --force to refetch)")
        return 0
    dest_root.mkdir(parents=True, exist_ok=True)

    print(f"[info] downloading {name} from {src['repo']}")
    data = _download(src["url"])
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    print(f"  {len(data)} bytes  sha256={digest[:16]}...")

    try:
        info = licence_report(tmp)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        print(f"[fail] {name} is not a readable VRM: {exc}")
        return 1

    print(f"  VRM {info['spec']}  name={info['name'] or '(unnamed)'}")
    print(f"  licence verdict: {info['verdict'].upper()}")
    print(f"  {info['detail']}")

    if info["verdict"] != "permissive" and not allow_restricted:
        tmp.unlink(missing_ok=True)
        print(f"[refused] {name} is {info['verdict']}, not permissive. "
              f"The file's own declaration is the evidence - pass "
              f"--allow-restricted only if you have checked it yourself.")
        return 2

    tmp.replace(dest)

    # The licence travels with the model, in a form that can be read later.
    stamp = _dt.datetime.now().isoformat(timespec="seconds")
    lines = [
        "VRM model licence record",
        "=" * 78,
        f"model            : {name}.vrm",
        f"source           : {src['repo']}",
        f"source file      : {src['url']}",
        f"installed        : {stamp}",
        f"sha256           : {digest}",
        "",
        "Declared by the model file itself (not by a README):",
        f"  VRM spec       : {info['spec']}",
        f"  name           : {info['name'] or '(unnamed)'}",
        f"  authors        : {info['authors']}",
        f"  copyright      : {info['copyright']}",
        f"  commercial use : {info['commercial']}",
        f"  modification   : {info['modification']}",
        f"  modification   : {info['modification']}",
        f"  allowRedistrib : {info['redistribution']}",
        f"  avatarPermit   : {info['avatarPermission']}",
        f"  creditNotation : {info['creditNotation']}",
        f"  licenceUrl     : {info['licenseUrl']}",
        f"  otherLicence   : {info['otherLicenseUrl']}",
        f"  verdict        : {info['verdict']}",
        f"  detail         : {info['detail']}",
        "",
        "Notes",
        "-" * 78,
        src["note"],
        "",
        "A model being hosted in an MIT-licensed repository does not make the",
        "avatar inside it MIT. The declaration above is the model's own.",
    ]
    # One record per model, named after it: several VRM models can sit side by
    # side in this folder, and a bare LICENSE.txt would have the last install
    # silently overwrite the previous model's provenance.
    (dest_root / f"LICENSE.{name}.txt").write_text("\n".join(lines),
                                                   encoding="utf-8")

    provenance = {
        "model": name,
        "file": f"{name}.vrm",
        "bytes": len(data),
        "sha256": digest,
        "source_repo": src["repo"],
        "source_file": src["url"],
        "installed_at": stamp,
        "licence": {
            "verdict": info["verdict"],
            "detail": info["detail"],
            "declared_in_file": {
                "spec": info["spec"],
                "name": info["name"],
                "authors": info["authors"],
                "copyright": info["copyright"],
                "commercialUsage": info["commercial"],
                "modification": info["modification"],
                "allowRedistribution": info["redistribution"],
                "avatarPermission": info["avatarPermission"],
                "creditNotation": info["creditNotation"],
                "licenseUrl": info["licenseUrl"],
                "otherLicenseUrl": info["otherLicenseUrl"],
            },
        },
    }
    (dest_root / f"PROVENANCE.{name}.json").write_text(
        json.dumps(provenance, indent=2), encoding="utf-8")

    readme = src.get("readme")
    if readme:
        try:
            (dest_root / f"SOURCE-README.{name}.md").write_bytes(
                _download(readme))
        except Exception as exc:
            print(f"[warn] could not store the source README: {exc}")

    print(f"[ok] {name}.vrm installed to {dest}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--allow-restricted", action="store_true")
    ap.add_argument("--root", default=None)
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else default_root()
    print(f"[info] VRM root: {root}")
    return install(args.model, root, force=args.force,
                   allow_restricted=args.allow_restricted)


if __name__ == "__main__":
    raise SystemExit(main())