"""
vendor_vrm_runtime.py

Copy the VRM renderer stack out of node_modules into assets/avatar_vrm/vendor/.

Why vendor at all
-----------------
This is a local desktop app. Loading the runtime from a CDN would make the
avatar depend on the network at *render* time, which is exactly the class of
dependency that breaks silently on a bad connection. So the ESM builds are
copied next to the page and resolved with an import map - no bundler, no
build step, no network at runtime.

Licence
-------
  three                       MIT   (three.js)
  @pixiv/three-vrm 3.5.5      MIT   (pixiv Inc.)
  its @pixiv/* sub-packages   MIT   (pixiv Inc.)

Genuinely open - this is the sharp contrast with Live2D, where the runtime is
proprietary and cannot be mirrored at all. Each package's LICENSE is copied
alongside so that claim travels with the files.

Usage:  py -3.11 vendor_vrm_runtime.py [--source <node_modules dir>]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEST = BASE / "assets" / "avatar_vrm" / "vendor"
PAGE = BASE / "assets" / "avatar_vrm" / "index.html"
DEFAULT_SOURCE = (Path(__file__).resolve().parent.parent / "node_modules")

# (package dir under node_modules, source file, destination name, npm version)
PACKAGES = [
    ("three", "build/three.module.js", "three.module.js", "0.186.1"),
    # three's ESM build is split: three.module.js holds the WebGL renderer and
    # re-exports the rest from three.core.js, so both files must be vendored or
    # importing "three" fails at fetch time.
    ("three", "build/three.core.js", "three.core.js", "0.186.1"),
    # GLTFLoader is an addon, not part of the "three" barrel, and it pulls two
    # utils via relative paths - so the addons keep their directory shape and
    # the import map exposes them under three/addons/.
    ("three", "examples/jsm/loaders/GLTFLoader.js",
     "addons/loaders/GLTFLoader.js", "0.186.1"),
    ("three", "examples/jsm/utils/BufferGeometryUtils.js",
     "addons/utils/BufferGeometryUtils.js", "0.186.1"),
    ("three", "examples/jsm/utils/SkeletonUtils.js",
     "addons/utils/SkeletonUtils.js", "0.186.1"),
    ("@pixiv/three-vrm", "lib/three-vrm.module.js",
     "three-vrm.module.js", "3.5.5"),
    ("@pixiv/three-vrm-core", "lib/three-vrm-core.module.js",
     "three-vrm-core.module.js", "3.5.5"),
    ("@pixiv/three-vrm-materials-mtoon",
     "lib/three-vrm-materials-mtoon.module.js",
     "three-vrm-materials-mtoon.module.js", "3.5.5"),
    ("@pixiv/three-vrm-materials-v0compat",
     "lib/three-vrm-materials-v0compat.module.js",
     "three-vrm-materials-v0compat.module.js", "3.5.5"),
    ("@pixiv/three-vrm-springbone", "lib/three-vrm-springbone.module.js",
     "three-vrm-springbone.module.js", "3.5.5"),
    ("@pixiv/three-vrm-node-constraint",
     "lib/three-vrm-node-constraint.module.js",
     "three-vrm-node-constraint.module.js", "3.5.5"),
]

# Bare specifiers used inside those builds, mapped to the flat vendor names.
IMPORT_MAP = {
    "three": "./three.module.js",
    "three/addons/": "./addons/",
    "@pixiv/three-vrm": "./three-vrm.module.js",
    "@pixiv/three-vrm-core": "./three-vrm-core.module.js",
    "@pixiv/three-vrm-materials-mtoon": "./three-vrm-materials-mtoon.module.js",
    "@pixiv/three-vrm-materials-v0compat":
        "./three-vrm-materials-v0compat.module.js",
    "@pixiv/three-vrm-springbone": "./three-vrm-springbone.module.js",
    "@pixiv/three-vrm-node-constraint": "./three-vrm-node-constraint.module.js",
}


def sync_inline_import_map() -> bool:
    """Rewrite the page's inline import map from IMPORT_MAP.

    Chromium ignores <script type="importmap" src=...>, so the page carries the
    map inline or its bare specifiers never resolve and it hangs on the boot
    overlay. Generating it here from the same table keeps the two from drifting.
    Returns True when the page was found and is now consistent.
    """
    if not PAGE.is_file():
        print(f"[warn] no avatar page at {PAGE} to sync")
        return False
    inline = {"imports": {k: "./vendor/" + v[2:] for k, v in IMPORT_MAP.items()}}
    block = ('<script type="importmap">\n'
             + json.dumps(inline, indent=2) + "\n</script>")
    html = PAGE.read_text(encoding="utf-8")
    new, count = re.subn(r'<script type="importmap">.*?</script>',
                         lambda _m: block, html, count=1, flags=re.S)
    if count == 0:
        print("[warn] the avatar page has no inline import map to update")
        return False
    if new != html:
        PAGE.write_text(new, encoding="utf-8")
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    args = ap.parse_args(argv)
    src_root = Path(args.source)

    if not src_root.is_dir():
        print(f"[fail] no node_modules at {src_root}\n"
              f"       npm install --prefix . three@0.186.1 @pixiv/three-vrm@3.5.5")
        return 1

    DEST.mkdir(parents=True, exist_ok=True)
    copied, missing = [], []

    for pkg, rel, name, version in PACKAGES:
        src = src_root / Path(pkg) / rel
        if not src.is_file():
            missing.append(pkg)
            continue
        target = DEST / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        # The licence travels with the code it covers.
        lic = src_root / Path(pkg) / "LICENSE.md"
        if not lic.is_file():
            lic = src_root / Path(pkg) / "LICENSE"
        if lic.is_file():
            shutil.copyfile(lic, DEST / f"{name}.LICENSE")
        copied.append({"package": pkg, "version": version, "file": name,
                       "bytes": target.stat().st_size})

    if missing:
        print(f"[warn] not vendored: {', '.join(missing)}")
        if len(missing) == len(PACKAGES):
            print("[fail] nothing vendored - is node_modules populated?")
            return 1

    manifest = {
        "source": str(src_root),
        "licence": "MIT (three.js and @pixiv/three-vrm, both pixiv Inc. / "
                   "three.js authors)",
        "note": "Vendored so the avatar does not need the network at render "
                "time. Each package's LICENSE is stored beside its build.",
        "packages": copied,
    }
    (DEST / "VENDOR.json").write_text(json.dumps(manifest, indent=2),
                                      encoding="utf-8")

    import_map = {"imports": IMPORT_MAP}
    (DEST / "import-map.json").write_text(json.dumps(import_map, indent=2),
                                          encoding="utf-8")
    sync_inline_import_map()

    total = sum(c["bytes"] for c in copied)
    print(f"[ok] vendored {len(copied)} module(s), {total // 1024} KB -> {DEST}")
    for c in copied:
        print(f"  {c['package']:38} {c['version']:8} {c['bytes'] // 1024:>6} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())