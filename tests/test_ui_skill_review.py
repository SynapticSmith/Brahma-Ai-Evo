"""Tests for Skill Review UI and Capability Grant Inspector (SEC-03, WS-1)."""

import os
import pytest

# Ensure Qt platform is offscreen for headless pytest runs
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication

from core.grants import verify_grant
from core.skill_store import (
    STATE_INSTALLED,
    STATE_REJECTED,
    SkillStore,
)
from core.dynamic_registry import DynamicToolRegistry
from core.ui_skill_review import (
    SkillReviewDialog,
    review_and_approve,
    review_and_reject,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["test"])
    yield app


@pytest.fixture
def test_skill_store(tmp_path):
    store = SkillStore(root_dir=tmp_path / "skills")
    return store


@pytest.fixture
def draft_artifact(test_skill_store):
    code = (
        '"""\nFeature: crypto_price\nDescription: Fetches crypto prices.\n"""\n\n'
        'FEATURE_METADATA = {"name": "crypto_price", "description": "Fetches crypto prices."}\n\n'
        'def execute(**kwargs):\n'
        '    return {"price": 50000}\n'
    )
    manifest = {
        "name": "crypto_price",
        "description": "Fetches crypto prices.",
        "version": "1.0.0",
        "author": "Autonomous Ultron Engine",
        "triggers": ["check crypto", "crypto price"],
        "dependencies": ["requests"],
    }
    test_cases = [{"input": {}, "expected": {"price": 50000}}]
    telemetry = {"passed": True, "duration": 0.05}

    artifact = test_skill_store.put_draft(
        name="crypto_price",
        code=code,
        manifest=manifest,
        test_cases=test_cases,
        telemetry=telemetry,
    )
    return artifact


def test_review_and_approve_headless(test_skill_store, draft_artifact):
    """Verifies headless review and approval promotes to installed and registers tool."""
    art_hash = draft_artifact.artifact_hash
    ok, msg = review_and_approve(
        artifact_hash=art_hash,
        capabilities=["exec.sandboxed", "net.outbound"],
        approved_by="admin_test",
        store=test_skill_store,
    )
    assert ok is True
    assert "crypto_price" in msg

    art = test_skill_store.get_artifact(art_hash)
    assert art.state == STATE_INSTALLED
    assert art.approved_by == "admin_test"
    assert art.grant is not None

    grant_ok, _ = verify_grant(art.grant, art_hash)
    assert grant_ok is True
    assert "net.outbound" in art.grant.get("capabilities", [])

    # DynamicToolRegistry now sees the tool
    assert DynamicToolRegistry.has_tool("crypto_price") is True


def test_review_and_reject_headless(test_skill_store, draft_artifact):
    """Verifies headless rejection transitions state and records reason."""
    art_hash = draft_artifact.artifact_hash
    ok = review_and_reject(
        artifact_hash=art_hash,
        reason="Forbidden telemetry call",
        store=test_skill_store,
    )
    assert ok is True

    art = test_skill_store.get_artifact(art_hash)
    assert art.state == STATE_REJECTED
    assert art.rejection_reason == "Forbidden telemetry call"


def test_skill_review_dialog_inspect_and_approve(qapp, test_skill_store, draft_artifact):
    """Verifies Qt dialog displays metadata and approval transitions state live."""
    dialog = SkillReviewDialog(artifact_hash=draft_artifact.artifact_hash, store=test_skill_store)
    dialog.show()

    assert "crypto_price" in dialog.name_lbl.text()
    assert draft_artifact.artifact_hash in dialog.hash_lbl.text()
    assert "Fetches crypto prices" in dialog.desc_lbl.text()
    assert "crypto_price" in dialog.code_edit.toPlainText()
    assert dialog.btn_approve.isEnabled() is True

    approved_events = []
    installed_events = []
    dialog.approved.connect(lambda h: approved_events.append(h))
    dialog.installed.connect(lambda h: installed_events.append(h))

    dialog.btn_approve.click()

    assert approved_events == [draft_artifact.artifact_hash]
    assert installed_events == [draft_artifact.artifact_hash]

    art = test_skill_store.get_artifact(draft_artifact.artifact_hash)
    assert art.state == STATE_INSTALLED
    assert "STATE: INSTALLED" in dialog.badge_lbl.text()
    assert dialog.btn_approve.isEnabled() is False
    dialog.close()


def test_skill_review_dialog_reject(qapp, test_skill_store, draft_artifact):
    """Verifies Qt dialog rejection transitions state and emits rejected signal."""
    dialog = SkillReviewDialog(artifact_hash=draft_artifact.artifact_hash, store=test_skill_store)
    dialog.show()

    rejected_events = []
    dialog.rejected.connect(lambda h: rejected_events.append(h))

    dialog.btn_reject.click()

    assert rejected_events == [draft_artifact.artifact_hash]
    art = test_skill_store.get_artifact(draft_artifact.artifact_hash)
    assert art.state == STATE_REJECTED
    assert "STATE: REJECTED" in dialog.badge_lbl.text()
    assert dialog.btn_approve.isEnabled() is False
    assert dialog.btn_reject.isEnabled() is False
    dialog.close()


def test_skill_review_dialog_tamper_detection(qapp, test_skill_store, draft_artifact):
    """Verifies dialog detects on-disk code modifications and blocks approval."""
    # Tamper with code.py on disk
    code_path = test_skill_store.store_dir / draft_artifact.artifact_hash / "code.py"
    code_path.write_text("TAMPERED_MALICIOUS_CONTENT = True\n", encoding="utf-8")

    dialog = SkillReviewDialog(artifact_hash=draft_artifact.artifact_hash, store=test_skill_store)
    dialog.show()

    assert "TAMPER" in dialog.ast_status_lbl.text().upper()
    assert dialog.btn_approve.isEnabled() is False
    dialog.close()


def test_skill_review_dialog_populate_from_store_drafts(qapp, test_skill_store, draft_artifact):
    """Verifies dialog without explicit hash discovers and populates all pending drafts."""
    dialog = SkillReviewDialog(store=test_skill_store)
    dialog.show()

    assert dialog.draft_combo.count() >= 1
    assert "crypto_price" in dialog.draft_combo.currentText()
    assert dialog.current_artifact is not None
    assert dialog.current_artifact.artifact_hash == draft_artifact.artifact_hash
    dialog.close()
