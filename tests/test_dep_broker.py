"""Tests for Dependency Supply Chain Broker and Lockfile (WS-3)."""

import subprocess
import pytest

from core.dep_broker import (
    DependencyBroker,
    DependencyPlan,
    parse_lockfile,
)
from core.skill_crucible import SkillCrucible


@pytest.fixture
def sample_lockfile(tmp_path):
    content = (
        "# Sample deps.lock\n"
        "requests==2.34.2 \\\n"
        "    --hash=sha256:0bf7b38d3845b4104d55b0a31da5f67b57b9e07894179e830e07172825d1e434\n\n"
        "pillow==12.3.0 \\\n"
        "    --hash=sha256:3d3d6cefa0459c2357d6b38c0379ea22e232938a98402dbfc956efebce1533ad\n"
    )
    p = tmp_path / "deps.lock"
    p.write_text(content, encoding="utf-8")
    return p


def test_parse_lockfile(sample_lockfile):
    """Verifies lockfile parser extracts packages and sha256 hashes accurately."""
    pkgs = parse_lockfile(sample_lockfile)
    assert "requests" in pkgs
    assert pkgs["requests"].version == "2.34.2"
    assert len(pkgs["requests"].hashes) == 1
    assert "sha256:0bf7b38d" in pkgs["requests"].hashes[0]

    assert "pillow" in pkgs
    assert pkgs["pillow"].version == "12.3.0"


def test_forge_and_crucible_never_invokes_pip(monkeypatch):
    """Acceptance test: verifying dependencies never calls pip."""
    def _fail_on_subprocess(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if "pip" in str(cmd):
            raise AssertionError(f"Crucible must NEVER call pip! Call: {cmd}")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fail_on_subprocess)

    # Calling resolve_dependencies on unknown or uninstalled packages must not call pip
    ok, msg = SkillCrucible.resolve_dependencies(["some_nonexistent_pkg_xyz"])
    assert ok is False
    assert "Missing dependencies" in msg


def test_package_not_in_lockfile_produces_dependency_request(sample_lockfile):
    """Acceptance test: package not in lockfile produces a dependency request, not an install."""
    broker = DependencyBroker(lockfile_path=sample_lockfile)

    # 'malicious_unknown_pkg' is not in sample_lockfile
    plan = broker.plan_dependencies(["malicious_unknown_pkg"])
    assert plan.is_satisfied is False
    assert "malicious-unknown-pkg" in plan.missing

    # Attempting to install plan fails immediately because package is unvetted
    ok, err = broker.install_plan(plan)
    assert ok is False
    assert "not in lockfile" in err
    assert "malicious-unknown-pkg" in err


def test_hash_mismatch_aborts_install(tmp_path, sample_lockfile):
    """Acceptance test: a wheel with a hash mismatch is aborted."""
    broker = DependencyBroker(lockfile_path=sample_lockfile)

    # Create dummy wheelhouse with tampered wheel
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    fake_wheel = wheelhouse / "requests-2.34.2-py3-none-any.whl"
    fake_wheel.write_bytes(b"TAMPERED_WHEEL_BYTES_CORRUPTED")

    plan = DependencyPlan(
        requested=["requests"],
        resolved={"requests": broker.locked_packages["requests"]},
        missing=[],
        satisfied_by_env=[],
        is_satisfied=False,
    )

    ok, msg = broker.install_plan(plan, wheelhouse_dir=wheelhouse)
    assert ok is False
    assert "hash mismatch" in msg.lower()


def test_source_distribution_rejected_under_only_binary(tmp_path, sample_lockfile):
    """Acceptance test: a package with only a source tar.gz is rejected under --only-binary."""
    broker = DependencyBroker(lockfile_path=sample_lockfile)

    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    tar_gz = wheelhouse / "requests-2.34.2.tar.gz"
    tar_gz.write_bytes(b"FAKE_TAR_GZ_SOURCE_DIST")

    plan = DependencyPlan(
        requested=["requests"],
        resolved={"requests": broker.locked_packages["requests"]},
        missing=[],
        satisfied_by_env=[],
        is_satisfied=False,
        only_binary=True,
    )

    ok, msg = broker.install_plan(plan, wheelhouse_dir=wheelhouse)
    assert ok is False
    assert "no binary wheel" in msg.lower()
    assert "--only-binary" in msg


def test_sbom_lists_full_closure(sample_lockfile):
    """Acceptance test: generated CycloneDX SBOM lists full dependency closure."""
    broker = DependencyBroker(lockfile_path=sample_lockfile)
    plan = broker.plan_dependencies(["requests", "pillow"])

    sbom = plan.sbom
    assert sbom.get("bomFormat") == "CycloneDX"
    assert sbom.get("specVersion") == "1.5"

    components = sbom.get("components", [])
    comp_names = [c["name"] for c in components]
    assert "requests" in comp_names
    assert "pillow" in comp_names

    for comp in components:
        assert comp.get("hashes")
        assert comp["hashes"][0]["alg"] == "SHA256"
