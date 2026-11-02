"""Packaging plumbing guards.

These are static checks: they do not build anything. Their job is to stop the
specific regressions that made the shipped binary unrebuildable from a clean
clone:

- a build script that calls a console script (``.venv\\Scripts\\pyinstaller.exe``)
  which does not exist in a freshly created venv;
- an unpinned packager, so the same source produces a different bundle;
- a CI workflow that silently becomes a required gate for every push even though
  a full PyInstaller build is minutes-to-tens-of-minutes;
- a "release" that ships without hashes.

See ARCHITECTURE-SPEC §10.5 and §12 Q1.7.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_build_requirements_pin_the_packager_and_its_hooks():
    text = _text("requirements-build.txt")

    assert re.search(r"^pyinstaller==\d", text, re.MULTILINE), "pyinstaller must be pinned exactly"
    assert re.search(
        r"^pyinstaller-hooks-contrib==\d", text, re.MULTILINE
    ), "hooks decide what is bundled; they must be pinned too"
    assert "-r requirements.txt" not in text, "build requirements must not pull runtime deps"


def test_dev_and_runtime_requirements_do_not_include_the_packager():
    for name in ("requirements.txt", "requirements-dev.txt"):
        text = _text(name).lower()
        assert "pyinstaller" not in text, f"{name} must not install the packager"


def _code_without_comments(name: str) -> str:
    """PowerShell comments may name the old bug; only executable lines matter."""
    lines = _text(name).splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def test_build_script_invokes_pyinstaller_through_the_interpreter():
    text = _code_without_comments("build_all.ps1")

    assert "Scripts\\pyinstaller.exe" not in text, (
        "a console-script path breaks after a venv rebuild; use python -m PyInstaller"
    )
    assert "-m\", \"PyInstaller\"" in text or "-m PyInstaller" in text
    assert "installer\\BrahmaEvo.spec" in text
    assert "installer\\BrahmaEvo_Setup.spec" in text


def test_build_script_checks_venv_python_311():
    text = _code_without_comments("build_all.ps1")

    assert '.venv\\Scripts\\python.exe' in text
    assert '$VenvVersion -ne "3.11"' in text
    assert "setup_dev.ps1 -Force" in text


def test_build_script_fails_loudly_and_writes_hashes():
    text = _text("build_all.ps1")

    assert "Test-Path $VenvPy" in text
    assert "setup_dev.ps1" in text, "the missing-venv message must name the fix"
    assert "SHA256SUMS.txt" in text
    assert "Get-FileHash" in text
    # Artifacts must match the spec shapes: BrahmaEvo.spec is a COLLECT (onedir)
    # and BrahmaEvo_Setup.spec is onefile.
    assert "dist\\BrahmaEvo\\BrahmaEvo.exe" in text
    assert "dist\\BrahmaEvo_Setup.exe" in text


def test_setup_script_pins_python_311_and_installs_all_three_sets():
    text = _text("scripts/setup_dev.ps1")

    assert "-3.11" in text
    assert 'if ($VenvVersion -ne "3.11")' in text
    for req in ("requirements.txt", "requirements-dev.txt", "requirements-build.txt"):
        assert req in text, f"setup_dev.ps1 must install {req}"


def test_build_workflow_is_manual_or_tag_driven_not_a_push_gate():
    text = _text(".github/workflows/build.yml")

    assert "workflow_dispatch" in text
    assert "pull_request" not in text, "packaging must not become a per-PR gate"
    assert "tags:" in text
    assert 'python-version: "3.11"' in text
    assert "SHA256SUMS.txt" in text
    assert "brahma-evo-windows-x64" in text


def test_referenced_specs_exist_and_match_their_build_shape():
    evo = _text("installer/BrahmaEvo.spec")
    setup = _text("installer/BrahmaEvo_Setup.spec")

    assert "COLLECT(" in evo, "BrahmaEvo.spec should remain onedir so Setup can embed it"
    assert "name='BrahmaEvo'" in evo
    assert "name='BrahmaEvo_Setup'" in setup
    assert "a.binaries" in setup, "the setup wizard is expected to be onefile"
