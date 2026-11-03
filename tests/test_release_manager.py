"""
Tests for Release Manager, Signed Manifests, and A/B Rollback (WS-6, SEC-11).
"""

import pytest

from core.plugin_sign import generate_keypair, sign_manifest
from core.release_manager import (
    ReleaseManager,
    ReleaseValidationError,
    compute_file_sha256,
)


@pytest.fixture
def release_env(tmp_path):
    releases_dir = tmp_path / "releases"
    priv_hex, pub_hex = generate_keypair()

    manager = ReleaseManager(
        releases_dir=releases_dir,
        trusted_keys={pub_hex},
        policy="manual",
        is_git_clone=False,
    )
    return manager, priv_hex, pub_hex, tmp_path


def make_valid_release(priv_hex: str, version: str = "1.1.0", channel: str = "stable"):
    artifact_content = b"fake binary payload for version " + version.encode()
    artifact_hash = compute_file_sha256(artifact_content)

    manifest = {
        "product": "BrahmaEvo",
        "version": version,
        "channel": channel,
        "commit": "abcdef1234567890",
        "published_at": "2026-11-02T00:00:00Z",
        "min_updater": "1.0.0",
        "artifacts": [
            {
                "name": "BrahmaEvo.zip",
                "sha256": artifact_hash,
                "size": len(artifact_content),
            }
        ],
        "rollout": {"cohort": "100%", "seed": version},
    }

    sig_hex = sign_manifest(priv_hex, manifest)
    return manifest, sig_hex, {"BrahmaEvo.zip": artifact_content}


def test_stage_and_activate_release(release_env):
    """Verifies complete stage and activation flow of a cryptographically signed release."""
    manager, priv_hex, pub_hex, _ = release_env

    manifest, sig_hex, artifacts = make_valid_release(priv_hex, version="1.1.0")

    staged_dir = manager.stage_release(manifest, sig_hex, artifacts)
    assert staged_dir.exists()
    assert (staged_dir / "release.json").exists()
    assert (staged_dir / "release.json.sig").exists()
    assert (staged_dir / "BrahmaEvo.zip").exists()

    state = manager.activate_version("1.1.0")
    assert state["current"] == "1.1.0"
    assert state["previous"] is None
    assert state["boot_failures"] == 0


def test_bad_signature_rejected(release_env):
    """Acceptance test: a release with a bad/forged signature is strictly rejected."""
    manager, priv_hex, _, _ = release_env

    manifest, _, artifacts = make_valid_release(priv_hex, version="1.2.0")

    # Forge with an unrelated private key
    untrusted_priv, _ = generate_keypair()
    bad_sig_hex = sign_manifest(untrusted_priv, manifest)

    with pytest.raises(ReleaseValidationError) as exc:
        manager.stage_release(manifest, bad_sig_hex, artifacts)
    assert "signature verification failed" in str(exc.value)


def test_hash_mismatch_rejected(release_env):
    """Acceptance test: a release artifact with a tampered SHA-256 is rejected."""
    manager, priv_hex, _, _ = release_env

    manifest, sig_hex, artifacts = make_valid_release(priv_hex, version="1.3.0")

    # Tamper with the artifact content
    tampered_artifacts = {"BrahmaEvo.zip": b"corrupted or malicious content"}

    with pytest.raises(ReleaseValidationError) as exc:
        manager.stage_release(manifest, sig_hex, tampered_artifacts)
    assert "hash mismatch" in str(exc.value)


def test_automatic_rollback_after_two_failures(release_env):
    """Acceptance test: 2 failed starts automatically roll back active version to previous."""
    manager, priv_hex, _, _ = release_env

    # 1. Activate v1.0.0
    m1, s1, a1 = make_valid_release(priv_hex, version="1.0.0")
    manager.stage_release(m1, s1, a1)
    manager.activate_version("1.0.0")

    # 2. Upgrade and activate v1.1.0
    m2, s2, a2 = make_valid_release(priv_hex, version="1.1.0")
    manager.stage_release(m2, s2, a2)
    manager.activate_version("1.1.0")

    state = manager.get_state()
    assert state["current"] == "1.1.0"
    assert state["previous"] == "1.0.0"

    # 3. Simulate first failed startup
    f1 = manager.record_boot_failure()
    assert f1 == 1
    assert manager.get_state()["current"] == "1.1.0"

    # 4. Simulate second failed startup -> triggers auto-rollback
    f2 = manager.record_boot_failure()
    assert f2 == 2
    state_after = manager.get_state()
    assert state_after["current"] == "1.0.0"
    assert state_after["previous"] == "1.1.0"
    assert state_after["boot_failures"] == 0


def test_manual_rollback(release_env):
    """Verifies intentional administrative rollback to previous version."""
    manager, priv_hex, _, _ = release_env

    m1, s1, a1 = make_valid_release(priv_hex, version="1.0.0")
    manager.stage_release(m1, s1, a1)
    manager.activate_version("1.0.0")

    m2, s2, a2 = make_valid_release(priv_hex, version="1.1.0")
    manager.stage_release(m2, s2, a2)
    manager.activate_version("1.1.0")

    rolled_back = manager.rollback()
    assert rolled_back == "1.0.0"
    assert manager.get_state()["current"] == "1.0.0"


def test_clone_install_blocks_auto_update(tmp_path):
    """Acceptance test: clone installs (development builds) prevent automatic update pulls."""
    priv_hex, pub_hex = generate_keypair()
    releases_dir = tmp_path / "releases"

    # is_git_clone = True
    clone_mgr = ReleaseManager(
        releases_dir=releases_dir,
        trusted_keys={pub_hex},
        policy="auto",
        is_git_clone=True,
    )

    allowed, reason = clone_mgr.check_policy("1.2.0", is_manual_override=False)
    assert allowed is False
    assert "Development build" in reason


def test_pin_version_policy(tmp_path):
    """Acceptance test: pin:<version> prevents updates to any non-matching version."""
    priv_hex, pub_hex = generate_keypair()
    releases_dir = tmp_path / "releases"

    pinned_mgr = ReleaseManager(
        releases_dir=releases_dir,
        trusted_keys={pub_hex},
        policy="pin:1.0.0",
        is_git_clone=False,
    )

    allowed_target, _ = pinned_mgr.check_policy("1.0.0")
    assert allowed_target is True

    blocked_target, reason = pinned_mgr.check_policy("1.1.0")
    assert blocked_target is False
    assert "version pin (1.0.0)" in reason


def test_rollout_cohort_eligibility(release_env):
    """Verifies that percentage cohorts gate release eligibility consistently."""
    manager, priv_hex, _, _ = release_env

    manifest_10pct = {
        "product": "BrahmaEvo",
        "version": "2.0.0",
        "channel": "stable",
        "commit": "12345",
        "published_at": "2026-11-02T00:00:00Z",
        "artifacts": [],
        "rollout": {"cohort": "10%", "seed": "2.0.0"},
    }

    # Test deterministic cohorting across install IDs
    results = [
        manager.is_cohort_eligible(manifest_10pct, f"device-user-{i}")
        for i in range(100)
    ]
    eligible_count = sum(results)
    # 10% cohort with SHA-256 hash bucket will allow roughly 5-15% of devices
    assert 5 <= eligible_count <= 18
