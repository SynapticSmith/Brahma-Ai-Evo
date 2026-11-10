#!/usr/bin/env python3
"""Setup and environment bootstrap for Brahma AI Evo.

Installs Python runtime requirements, Playwright headless browsers, and
recommended external system tools (ripgrep, FFmpeg, yt-dlp, pandoc, sqlite3)
via winget on Windows.

Usage:
    python setup.py                     # Full setup
    python setup.py --skip-system-tools # Skip winget external tool installs
    python setup.py --check-only        # Check status of all dependencies
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# The pinned runtime stack (numpy 2.4, PyQt6 6.11, opencv 5.0) publishes wheels
# for 3.11+ only. Fail here, with a clear message, instead of letting pip resolve
# into a wall of "no matching distribution" errors.
if sys.version_info < (3, 11):
    sys.exit(
        "Brahma AI Evo needs Python 3.11 or newer; this interpreter is "
        f"{sys.version.split()[0]}.\n"
        "Install 3.11+ and re-run: py -3.11 setup.py"
    )

REPO_ROOT = Path(__file__).resolve().parent

RECOMMENDED_SYSTEM_TOOLS = [
    ("rg", "BurntSushi.ripgrep.MSVC", "ripgrep (ultra-fast code and symbol search)"),
    ("ffmpeg", "yt-dlp.FFmpeg", "FFmpeg (audio/video processing & normalization)"),
    ("yt-dlp", "yt-dlp.yt-dlp", "yt-dlp (multimodal subtitle & media crawler)"),
    ("pandoc", "JohnMacFarlane.Pandoc", "Pandoc (universal document conversion)"),
    ("sqlite3", "SQLite.SQLite", "SQLite CLI (database inspection & FTS tuning)"),
    ("openscad", "OpenSCAD.OpenSCAD", "OpenSCAD (headless 3D CAD modeling & STL compilation)"),
]


def refresh_windows_path() -> None:
    """Refresh os.environ['PATH'] from Windows Machine and User registry."""
    if sys.platform != "win32":
        return
    try:
        import winreg

        parts = []
        for hkey, subkey in [
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            (winreg.HKEY_CURRENT_USER, r"Environment"),
        ]:
            try:
                with winreg.OpenKey(hkey, subkey) as key:
                    val, _ = winreg.QueryValueEx(key, "Path")
                    if val:
                        parts.append(val)
            except OSError:
                continue
        if parts:
            os.environ["PATH"] = ";".join(parts)
    except Exception:
        pass


def install_openscad_portable() -> bool:
    """Download and install OpenSCAD portable zip into %LOCALAPPDATA%\\OpenSCAD."""
    import tempfile
    import urllib.request
    import zipfile

    url = "https://files.openscad.org/OpenSCAD-2021.01-x86-64.zip"
    local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    target_dir = Path(local_app_data) / "OpenSCAD"

    print("  Downloading OpenSCAD portable zip...")
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            urllib.request.urlretrieve(url, tmp_path)

        print(f"  Extracting to {local_app_data}...")
        with zipfile.ZipFile(tmp_path, "r") as z:
            z.extractall(local_app_data)
        tmp_path.unlink(missing_ok=True)

        extracted_dir = Path(local_app_data) / "openscad-2021.01"
        if extracted_dir.is_dir() and not target_dir.is_dir():
            extracted_dir.rename(target_dir)

        # Register in user PATH
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS) as key:
                try:
                    curr_path, _ = winreg.QueryValueEx(key, "Path")
                except OSError:
                    curr_path = ""
                if str(target_dir) not in curr_path:
                    new_path = f"{curr_path};{target_dir}" if curr_path else str(target_dir)
                    winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
            refresh_windows_path()

        print("  [OK] Successfully installed OpenSCAD portable.")
        return True
    except Exception as exc:
        print(f"  [WARN] Failed to install OpenSCAD portable: {exc}")
        return False


def check_and_install_system_tools(skip_install: bool = False, check_only: bool = False) -> None:
    """Audit and optionally install recommended external CLI tools via winget / portable download."""
    print("\n--- Checking Recommended External System Tools ---")
    refresh_windows_path()

    missing = []
    for binary, pkg_id, desc in RECOMMENDED_SYSTEM_TOOLS:
        path = shutil.which(binary)
        if path:
            print(f"  [OK] {binary:<8} -> {desc}\n       Location: {path}")
        else:
            print(f"  [MISSING] {binary:<8} -> {desc}")
            missing.append((binary, pkg_id, desc))

    if not missing:
        print("\nAll recommended external system tools are installed.")
        return

    if check_only or skip_install:
        if missing:
            print(f"\n{len(missing)} recommended tool(s) missing. Run setup.py without --skip-system-tools to install.")
        return

    if sys.platform != "win32":
        print(f"\n{len(missing)} tool(s) missing. Please install via your system package manager (e.g. brew/apt).")
        return

    print(f"\nInstalling {len(missing)} missing system tool(s)...")
    winget_path = shutil.which("winget")

    for binary, pkg_id, desc in missing:
        if binary == "openscad":
            install_openscad_portable()
            continue

        if not winget_path:
            print(f"  [WARN] winget not found. Please install {binary} manually.")
            continue

        print(f"  Installing {binary} ({pkg_id}) via winget...")
        cmd = [
            winget_path,
            "install",
            "--id",
            pkg_id,
            "--exact",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--silent",
        ]
        try:
            res = subprocess.run(cmd, check=False)
            if res.returncode == 0:
                print(f"  [OK] Successfully installed {binary}.")
            else:
                print(f"  [WARN] winget exited with code {res.returncode} for {binary}.")
        except Exception as exc:
            print(f"  [WARN] Failed to invoke winget for {binary}: {exc}")

    refresh_windows_path()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Brahma AI Evo development environment")
    parser.add_argument("--skip-pip", action="store_true", help="Skip pip install of requirements.txt")
    parser.add_argument("--skip-playwright", action="store_true", help="Skip playwright install")
    parser.add_argument("--skip-system-tools", action="store_true", help="Skip winget external tool installs")
    parser.add_argument("--check-only", action="store_true", help="Only check dependency status without installing")
    args = parser.parse_args()

    print("============================================================")
    print(" Brahma AI Evo - Environment Bootstrap & Setup")
    print("============================================================")
    print(f"Python interpreter: {sys.executable} ({sys.version.split()[0]})")

    # 1. Python dependencies
    if not args.skip_pip and not args.check_only:
        req_file = REPO_ROOT / "requirements.txt"
        if req_file.is_file():
            print("\n--- Installing Python Requirements ---")
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req_file)], check=True)
        else:
            print(f"\nWarning: {req_file} not found.")

    # 2. Playwright browsers
    if not args.skip_playwright and not args.check_only:
        print("\n--- Verifying Playwright Headless Browsers ---")
        try:
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        except Exception as exc:
            print(f"Warning: Playwright browser install failed: {exc}")

    # 3. External CLI tools
    check_and_install_system_tools(skip_install=args.skip_system_tools, check_only=args.check_only)

    print("\n============================================================")
    print("Setup check complete! Run 'python main.py' to start Brahma Evo.")
    print("============================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
