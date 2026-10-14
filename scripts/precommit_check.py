"""
scripts/precommit_check.py

Is this repository safe to commit? Answer that before `git add`, not after.

    py -3.11 scripts/precommit_check.py            # full run
    py -3.11 scripts/precommit_check.py --quick    # skip the test suite

Exit code 0 means nothing blocking was found. Anything non-zero means DO NOT
COMMIT until it is dealt with - the point of this script is that a credential
should be caught here rather than in a git history forever.

Why a script and not a checklist
--------------------------------
Because the failure this exists to prevent has already happened once on this
repo: two live Synapse credentials sat in a git-tracked settings file while the
whole conversation went round on whether to commit. Every check below is
something that is easy to believe is fine and is not.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Anything matching these is a credential, not a string that looks like one.
SECRET_PATTERNS = [
    (r"\bsk-[A-Za-z0-9]{16,}", "OpenAI-style key"),
    (r"\bAIza[0-9A-Za-z_-]{25,}", "Google API key"),
    (r"\bgh[pousr]_[A-Za-z0-9]{20,}", "GitHub token"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", "bearer token"),
    (r"\bhf_[A-Za-z0-9]{30,}", "HuggingFace token"),
    (r"(?i)\baws_secret_access_key\s*=\s*\S{20,}", "AWS secret"),
]
COMPILED_SECRETS = [(re.compile(p), name) for p, name in SECRET_PATTERNS]

# Things that are supposed to be there. The secret scanner is line-based and
# honest files talk about tokens in prose; these paths are exempt by path, not
# by pattern, so an exemption cannot hide a real key in a checked-in file.
EXEMPT_PATHS = {
    "assets/avatar_vrm/vendor",     # third-party MIT builds + their licences
    ".git", "node_modules", "__pycache__", ".pytest_cache",
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache",
             ".mypy_cache", ".ruff_cache", ".playwright-mcp", "build", "dist"}
SKIP_SUFFIX = {".pyc", ".pyo", ".part", ".png", ".jpg", ".jpeg", ".gif",
               ".ico", ".vrm", ".moc3", ".ttf", ".otf", ".woff", ".woff2",
               ".onnx", ".mp3", ".wav", ".exe", ".dll", ".pyd", ".zip"}

# Files >1 MB are called out rather than blocked: the vendored VRM runtime is
# legitimately 2.4 MB and must be committed with its licences.
LARGE_FILE_KB = 1024

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
    GREEN = RED = YELLOW = DIM = RESET = ""


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(p)


def candidates() -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            rp = rel(p)
            if any(rp == e or rp.startswith(e + "/") for e in EXEMPT_PATHS):
                continue
            if p.suffix.lower() in SKIP_SUFFIX:
                continue
            out.append(p)
    return out


def git(*args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=120).stdout.strip()
    except Exception as exc:
        return f"<git failed: {exc}>"


def check_secrets() -> list:
    """Blockers: a credential anywhere in a file that would be committed."""
    blockers = []
    for path in candidates():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if len(text) > 4_000_000:
            continue
        for rx, name in COMPILED_SECRETS:
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                val = m.group(0)
                shown = val[:8] + "..." + val[-3:]
                blockers.append((rel(path), line, name, shown))
    return blockers


def check_ignored_leftovers() -> list:
    """Advisory: files git is currently ignoring that look like real work."""
    out = subprocess.run(["git", "status", "--short", "--ignored"],
                         cwd=ROOT, capture_output=True, text=True,
                         timeout=120).stdout
    rows = []
    for line in out.splitlines():
        if line.startswith("!!"):
            rp = line[3:].strip().replace("\\", "/")
            if rp in ("node_modules/",) or rp.endswith("/"):
                continue
            size = 0
            target = ROOT / rp
            if target.is_file():
                size = target.stat().st_size
            elif target.is_dir():
                size = sum(f.stat().st_size for f in target.rglob("*")
                           if f.is_file())
            rows.append((rp, size // 1024))
    return rows


def check_large_files() -> list:
    out = []
    for path in candidates():
        try:
            size = path.stat().st_size
        except Exception:
            continue
        if size // 1024 >= LARGE_FILE_KB:
            out.append((rel(path), size // 1024))
    return sorted(out, key=lambda r: -r[1])


def check_vendor_licences() -> list:
    """Blocker: a vendored build committed without the licence it is under."""
    vendor = ROOT / "assets" / "avatar_vrm" / "vendor"
    if not vendor.is_dir():
        return []
    blockers = []
    for js in vendor.glob("*.js"):
        if js.name == "import-map.json":
            continue
        if not (vendor / f"{js.name}.LICENSE").is_file():
            blockers.append(rel(js))
    return blockers


def check_model_sidecars() -> list:
    """Advisory: an installed model with no licence record beside it.

    Uses the same %LOCALAPPDATA% location the app does, so it reports what the
    running app would find, not what the repo contains.
    """
    out = []
    root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BrahmaAI" / "avatar"
    if not root.is_dir():
        return out
    for folder in sorted(root.rglob("*")):
        if not folder.is_dir():
            continue
        models = [f for f in folder.iterdir()
                  if f.suffix.lower() in (".vrm",)
                  or any(c.suffix == ".model3.json" for c in folder.rglob("*.model3.json"))]
        if not models:
            continue
        has = any(f.name.upper().startswith(("LICENSE", "COPYING"))
                  for f in folder.iterdir() if f.is_file())
        if not has:
            out.append(rel_assets(folder, root))
    return out


def rel_assets(folder: Path, root: Path) -> str:
    try:
        return str(folder.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(folder)


def check_template_has_no_credentials() -> list:
    """Blocker, specifically for this repo's known trap.

    config/app_settings.json is tracked by git and is NOT the file the app
    reads at runtime (config_manager resolves to %LOCALAPPDATA%\\BrahmaAI\\config).
    That mismatch is exactly why credentials sat in it unnoticed: it looks like
    the config and it is not.
    """
    p = ROOT / "config" / "app_settings.json"
    if not p.is_file():
        return []
    text = p.read_text(encoding="utf-8", errors="replace")
    hits = []
    for rx, name in COMPILED_SECRETS:
        for m in rx.finditer(text):
            val = m.group(0)
            hits.append((rel(p), text.count("\n", 0, m.start()) + 1, name,
                         val[:8] + "..." + val[-3:]))
    return hits


def check_tests(quick: bool) -> list:
    if quick:
        return []
    notes = []
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"],
                           cwd=ROOT, capture_output=True, text=True, timeout=900)
        tail = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
        notes.append(("pytest tests", r.returncode == 0,
                      tail[-1] if tail else f"rc={r.returncode}"))
    except Exception as exc:
        notes.append(("pytest tests", False, str(exc)))

    for h in ("accept_391.py", "accept_avatar.py", "accept_instructions.py",
              "accept_providers.py", "accept_local_speech.py",
              "accept_live_toggle.py", "accept_wakeword.py"):
        path = ROOT / h
        if not path.is_file():
            continue
        try:
            r = subprocess.run([sys.executable, h], cwd=ROOT, capture_output=True,
                               text=True, timeout=900)
            ok = "ALL CRITERIA PASS" in r.stdout
            note = next((l for l in r.stdout.splitlines()
                         if l.startswith("RESULT")), f"rc={r.returncode}")
            notes.append((h, ok, note.strip()))
        except Exception as exc:
            notes.append((h, False, str(exc)))
    return notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true",
                    help="skip the test suite (static checks only)")
    args = ap.parse_args(argv)

    blockers: list = []
    warnings: list = []

    print("=" * 74)
    print("ONE-391 pre-commit check")
    print("=" * 74)

    secrets = check_secrets() + check_template_has_no_credentials()
    print(f"\n{'-' * 74}\n1. Credential scan (all committable files)")
    if secrets:
        for path, line, name, shown in secrets:
            print(f"  {RED}BLOCK{RESET}  {path}:{line}  {name}  {shown}")
        blockers.append(f"{len(secrets)} credential(s) in committable files")
    else:
        print(f"  {GREEN}OK{RESET}  no credentials found")

    print(f"\n{'-' * 74}\n2. Vendored third-party licences")
    missing = check_vendor_licences()
    if missing:
        for p in missing:
            print(f"  {RED}BLOCK{RESET}  {p} has no .LICENSE beside it")
        blockers.append(f"{len(missing)} vendored build(s) without a licence")
    else:
        print(f"  {GREEN}OK{RESET}  every vendored build carries its licence")

    print(f"\n{'-' * 74}\n3. Installed models have a licence record")
    sidecars = check_model_sidecars()
    if sidecars:
        for p in sidecars:
            print(f"  {YELLOW}WARN{RESET}  {p} - no LICENSE beside the model")
        warnings.append(f"{len(sidecars)} model folder(s) without a licence")
    else:
        print(f"  {GREEN}OK{RESET}  every installed model folder has a licence")

    print(f"\n{'-' * 74}\n4. Large files (>= {LARGE_FILE_KB} KB)")
    large = check_large_files()
    if large:
        for p, kb in large:
            print(f"  {YELLOW}WARN{RESET}  {p}  {kb} KB")
        warnings.append(f"{len(large)} file(s) >= {LARGE_FILE_KB} KB")
    else:
        print(f"  {GREEN}OK{RESET}  none")

    print(f"\n{'-' * 74}\n5. Working tree")
    dirty = [l for l in git("status", "--short").splitlines() if l.strip()]
    staged = [l for l in dirty if l[:1] not in (" ", "?", "!")]
    untracked = [l for l in dirty if l.startswith("??")]
    print(f"  {DIM}modified/staged: {len(dirty) - len(untracked)}"
          f"   untracked: {len(untracked)}{RESET}")
    ignored = check_ignored_leftovers()
    if ignored:
        print(f"  {YELLOW}WARN{RESET}  ignored files present (confirm intended):")
        for p, kb in ignored[:10]:
            print(f"        {p}  {kb} KB")

    print(f"\n{'-' * 74}\n6. Verification")
    notes = check_tests(args.quick)
    if not notes:
        print(f"  {DIM}skipped (--quick){RESET}")
    for name, ok, detail in notes:
        mark = f"{GREEN}OK{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  {mark}  {name:26} {detail}")
        if not ok:
            blockers.append(f"{name} did not pass")

    print("\n" + "=" * 74)
    if blockers:
        print(f"{RED}DO NOT COMMIT{RESET} - {len(blockers)} blocker(s):")
        for b in blockers:
            print(f"  - {b}")
    else:
        print(f"{GREEN}SAFE TO COMMIT{RESET}"
              f"{YELLOW} ({len(warnings)} warning(s) to review){RESET}" if warnings
              else f"{GREEN}SAFE TO COMMIT{RESET}")
    print("=" * 74)
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())