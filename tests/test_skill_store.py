"""Tests for content-addressed SkillStore and cryptographic capability grants (SEC-01, SEC-02)."""

from __future__ import annotations

import datetime
from pathlib import Path

from core.grants import issue_grant, verify_grant
from core.skill_store import (
    STATE_APPROVED,
    STATE_DRAFT,
    STATE_INSTALLED,
    STATE_QUARANTINE,
    STATE_REJECTED,
    SkillStore,
    compute_artifact_hash,
)


def test_compute_artifact_hash_deterministic():
    """Deterministic canonical hashing across runs and ordering."""
    code = "def execute(**kwargs):\n    return 'hello'\n"
    manifest1 = {"name": "test_skill", "version": "1.0.0", "parameters": {}}
    manifest2 = {"parameters": {}, "version": "1.0.0", "name": "test_skill"}
    tests = [{"input": {"a": 1}}]

    h1 = compute_artifact_hash("test_skill", code, manifest1, tests)
    h2 = compute_artifact_hash("test_skill", code, manifest2, tests)
    assert h1 == h2
    assert len(h1) == 64

    # 1 byte difference produces distinct hash
    h3 = compute_artifact_hash("test_skill", code + " ", manifest1, tests)
    assert h1 != h3


def test_grant_issuance_and_verification():
    """Verify grant life cycle, hash verification, expiry, and capability checks."""
    dummy_hash = "a" * 64
    grant = issue_grant(dummy_hash, capabilities=["fs.read:Documents", "net.outbound:api.example.com"], ttl_days=7)

    assert grant["artifact_hash"] == dummy_hash
    assert grant["approved_by"] == "local_user"
    assert grant["nonce"]

    # Valid check
    ok, reason = verify_grant(grant, dummy_hash)
    assert ok is True
    assert "Grant valid" in reason

    # Capability check
    ok_cap, _ = verify_grant(grant, dummy_hash, required_capability="fs.read:Documents")
    assert ok_cap is True

    bad_cap, msg_cap = verify_grant(grant, dummy_hash, required_capability="fs.write:System")
    assert bad_cap is False
    assert "not granted" in msg_cap

    # Wildcard prefix check
    wildcard_grant = issue_grant(dummy_hash, capabilities=["fs.read:*"])
    ok_wc, _ = verify_grant(wildcard_grant, dummy_hash, required_capability="fs.read:Desktop")
    assert ok_wc is True

    # Tampered code hash mismatch
    tampered_hash = "b" * 64
    ok_tamper, msg_tamper = verify_grant(grant, tampered_hash)
    assert ok_tamper is False
    assert "integrity compromised" in msg_tamper

    # Expired grant
    expired_grant = dict(grant)
    expired_grant["expires_at"] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).isoformat()
    ok_exp, msg_exp = verify_grant(expired_grant, dummy_hash)
    assert ok_exp is False
    assert "expired" in msg_exp


def test_skill_store_lifecycle_and_tamper_detection(tmp_path: Path):
    """Test draft -> approved -> installed state transitions and tampering defense."""
    store = SkillStore(root_dir=tmp_path)
    code = "def execute(**kwargs):\n    return 'result'\n"
    manifest = {"name": "demo_tool", "parameters": {}}
    tests = [{"input": {}}]

    # 1. Put draft
    artifact = store.put_draft("demo_tool", code, manifest, tests)
    assert artifact.state == STATE_DRAFT
    assert (store.store_dir / artifact.artifact_hash / "code.py").exists()

    # Direct install from draft must fail
    can_install, msg = store.install_artifact(artifact.artifact_hash)
    assert can_install is False
    assert "must be approved first" in msg

    # 2. Approve draft
    ok_app, grant, msg_app = store.approve_artifact(artifact.artifact_hash, capabilities=["fs.read"])
    assert ok_app is True
    assert grant is not None
    updated = store.get_artifact(artifact.artifact_hash)
    assert updated.state == STATE_APPROVED

    # 3. Install approved artifact
    can_install2, msg2 = store.install_artifact(artifact.artifact_hash)
    assert can_install2 is True
    installed = store.get_artifact(artifact.artifact_hash)
    assert installed.state == STATE_INSTALLED

    # 4. Verify installed artifact integrity
    ok_ver, art_ver, reason = store.verify_installed_artifact("demo_tool")
    assert ok_ver is True
    assert art_ver.artifact_hash == artifact.artifact_hash

    # 5. Tampering defense: mutate code.py on disk by 1 byte
    code_path = store.store_dir / artifact.artifact_hash / "code.py"
    code_path.write_text(code + "\n# malicious injection\n", encoding="utf-8")

    # Verify must catch tampering and quarantine the artifact!
    ok_tampered, art_tampered, reason_tampered = store.verify_installed_artifact("demo_tool")
    assert ok_tampered is False
    assert "tampered" in reason_tampered.lower()

    quarantined = store.get_artifact(artifact.artifact_hash)
    assert quarantined.state == STATE_QUARANTINE


def test_skill_store_rejection_and_audit_logging(tmp_path: Path):
    """Rejected skills record reasons and emit append-only audit entries."""
    store = SkillStore(root_dir=tmp_path)
    code = "def execute(**kwargs):\n    return 1\n"
    manifest = {"name": "risky_tool"}

    artifact = store.put_draft("risky_tool", code, manifest)
    ok = store.reject_artifact(artifact.artifact_hash, reason="Requests excessive privileges")
    assert ok is True

    rejected = store.get_artifact(artifact.artifact_hash)
    assert rejected.state == STATE_REJECTED
    assert rejected.rejection_reason == "Requests excessive privileges"

    # Audit file contains records
    audit_file = store.audit_file
    assert audit_file.exists()
    lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 2  # DRAFT_STAGED, STATE_TRANSITION


def test_dynamic_registry_loads_only_verified_installed_skills(tmp_path: Path, monkeypatch):
    """DynamicToolRegistry only registers synthetic skills if installed and verified."""
    import core.dynamic_registry as dyn_reg
    from core.dynamic_registry import DynamicToolRegistry

    features_dir = tmp_path / "features"
    features_dir.mkdir(parents=True, exist_ok=True)
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(dyn_reg, "FEATURES_DIR", features_dir)
    monkeypatch.setattr(dyn_reg, "APPDATA_SKILLS_DIR", skills_root)

    store = SkillStore(root_dir=skills_root)
    monkeypatch.setattr("core.skill_store.get_user_data_dir", lambda: tmp_path)

    code = "def execute(**kwargs):\n    return 'installed_result'\n"
    manifest = {"name": "synthetic_installed", "active": True, "parameters": {}}

    # 1. Draft skill is NOT loaded
    store.put_draft("synthetic_installed", code, manifest)
    DynamicToolRegistry._initialized = False
    DynamicToolRegistry._skills.clear()
    DynamicToolRegistry.initialize()
    assert not DynamicToolRegistry.has_tool("synthetic_installed")

    # 2. Approved and Installed skill IS loaded
    art = store.get_artifact_by_name("synthetic_installed")
    store.approve_artifact(art.artifact_hash)
    store.install_artifact(art.artifact_hash)

    DynamicToolRegistry._initialized = False
    DynamicToolRegistry._skills.clear()
    DynamicToolRegistry.initialize()
    assert DynamicToolRegistry.has_tool("synthetic_installed")
