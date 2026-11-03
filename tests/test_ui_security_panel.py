"""Tests for Security Panel and Audit Inspector UI (SEC-17, core/ui_security_panel.py).

Validates:
1. SecurityPanelDialog initialization and header/stats layout.
2. Population of audit log records from append-only chain.
3. Filtering of events (Denials, Approvals, Secrets).
4. Integrity verification reporting (valid chain vs tampered).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from core.audit import AuditLogger
from core.ui_security_panel import SecurityPanelDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_security_panel_dialog_loads_and_displays_events(tmp_path: Path, qapp):
    """SecurityPanelDialog loads records, counts categories, and verifies chain."""
    audit_file = tmp_path / "audit.jsonl"
    audit = AuditLogger(audit_file)

    audit.log("SYSTEM_START", caller="core", version="1.0.0")
    audit.log("SCOPE_GRANTED", caller="admin", scope="model:gemini")
    audit.log("SECRET_ACCESSED", caller="skill_engine", secret_id="gemini_key")
    audit.log("POLICY_DENIED", caller="untrusted_script", reason="Illegal file write")
    audit.log("GATEWAY_LOCKOUT", caller="gateway", client_ip="10.0.0.1")

    dialog = SecurityPanelDialog(audit_path=audit_file)

    # Check stats labels
    assert "Total Events: 5" in dialog.stat_total.text()
    assert "Approvals: 1" in dialog.stat_approvals.text()
    assert "Denials: 1" in dialog.stat_denials.text()
    assert "Violations: 1" in dialog.stat_violations.text()

    # Table contains 5 rows
    assert dialog.table.rowCount() == 5

    # Integrity badge indicates valid chain
    assert "Valid" in dialog.integrity_badge.text()

    # Test filtering: Denials & Violations
    dialog.filter_combo.setCurrentText("Denials & Violations")
    assert dialog.table.rowCount() == 2  # POLICY_DENIED and GATEWAY_LOCKOUT

    # Test filtering: Approvals & Grants
    dialog.filter_combo.setCurrentText("Approvals & Grants")
    assert dialog.table.rowCount() == 1  # SCOPE_GRANTED

    # Test filtering: Secret Operations
    dialog.filter_combo.setCurrentText("Secret Operations")
    assert dialog.table.rowCount() == 1  # SECRET_ACCESSED

    # Reset to All Events
    dialog.filter_combo.setCurrentText("All Events")
    assert dialog.table.rowCount() == 5

    dialog.close()


def test_security_panel_detects_tampered_log(tmp_path: Path, qapp):
    """When audit log file has been modified externally, the panel displays tamper alert."""
    audit_file = tmp_path / "audit.jsonl"
    audit = AuditLogger(audit_file)

    audit.log("STEP_1", caller="core")
    audit.log("STEP_2", caller="core")

    # Tamper with the log file
    lines = audit_file.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["details"]["tampered"] = True
    lines[0] = json.dumps(rec)
    audit_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    dialog = SecurityPanelDialog(audit_path=audit_file)
    assert "TAMPER DETECTED" in dialog.integrity_badge.text()
    dialog.close()
