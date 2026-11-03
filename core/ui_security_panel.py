"""
Security Panel & Audit Inspector Dialog (SEC-17, Q4 Assurance).

Part of Security Architecture Phase 3 / Project Ultron.
Provides an interactive management interface for viewing:
1. Append-only cryptographic audit events (recent approvals, denials, sandbox violations).
2. Hash-chain integrity status and tamper verification (`verify_audit_log`).
3. Capability grants, active scopes, and caller security status.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.audit import get_audit_logger, verify_audit_log
from core.ui_theme import C

logger = logging.getLogger("SecurityPanel")


class SecurityPanelDialog(QDialog):
    """Interactive Security Panel displaying audit events and chain integrity."""

    def __init__(self, parent: Optional[QWidget] = None, audit_path: Optional[Path] = None):
        super().__init__(parent)
        self.audit_path = (
            Path(audit_path).resolve()
            if audit_path is not None
            else get_audit_logger().log_path
        )
        self.setWindowTitle("Brahma AI — Security & Audit Console")
        self.resize(960, 620)
        self.setMinimumSize(800, 500)
        self.setStyleSheet(
            f"QDialog {{ background-color: {C.BG}; color: {C.ICE}; }}"
            f"QLabel {{ color: {C.ICE}; }}"
            f"QTableWidget {{ background-color: {C.PANEL}; color: {C.ICE}; border: 1px solid {C.BORDER}; gridline-color: {C.BORDER}; }}"
            f"QHeaderView::section {{ background-color: {C.DARK}; color: {C.MUTED_C}; border: 1px solid {C.BORDER}; padding: 4px; font-weight: bold; }}"
            f"QComboBox {{ background-color: {C.PANEL}; color: {C.ICE}; border: 1px solid {C.BORDER}; padding: 4px; border-radius: 4px; }}"
            f"QPushButton {{ background-color: {C.PANEL}; color: {C.ICE}; border: 1px solid {C.BORDER}; padding: 6px 14px; border-radius: 4px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: {C.BORDER}; border-color: {C.ACC}; }}"
        )

        self._records: List[Dict[str, Any]] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(14)

        # Header Title
        title_row = QHBoxLayout()
        shield_lbl = QLabel("🛡️ SECURITY & AUDIT CONSOLE")
        shield_lbl.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        title_row.addWidget(shield_lbl)
        title_row.addStretch()

        self.integrity_badge = QLabel("[Verifying chain...]")
        self.integrity_badge.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        title_row.addWidget(self.integrity_badge)
        main_layout.addLayout(title_row)

        # Stats Cards Bar
        stats_frame = QFrame()
        stats_frame.setStyleSheet(
            f"QFrame {{ background-color: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 6px; padding: 6px; }}"
        )
        stats_layout = QHBoxLayout(stats_frame)
        stats_layout.setContentsMargins(12, 8, 12, 8)

        self.stat_total = QLabel("Total Events: 0")
        self.stat_approvals = QLabel("Approvals: 0")
        self.stat_denials = QLabel("Denials: 0")
        self.stat_violations = QLabel("Violations: 0")

        for lbl, color in [
            (self.stat_total, C.ICE),
            (self.stat_approvals, C.GREEN),
            (self.stat_denials, C.RED),
            (self.stat_violations, C.ACC),
        ]:
            lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            lbl.setStyleSheet(f"color: {color};")
            stats_layout.addWidget(lbl)
            stats_layout.addStretch()

        main_layout.addWidget(stats_frame)

        # Filter Controls Row
        filter_row = QHBoxLayout()
        filter_lbl = QLabel("Filter:")
        filter_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        filter_row.addWidget(filter_lbl)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems([
            "All Events",
            "Denials & Violations",
            "Approvals & Grants",
            "Secret Operations",
            "Gateway & Network",
        ])
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_combo)

        filter_row.addStretch()

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh)
        filter_row.addWidget(refresh_btn)

        verify_btn = QPushButton("🔍 Full Verify")
        verify_btn.clicked.connect(self._verify_chain_dialog)
        filter_row.addWidget(verify_btn)

        main_layout.addLayout(filter_row)

        # Audit Records Table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Seq",
            "Timestamp",
            "Event",
            "Caller",
            "Details",
            "Hash",
        ])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)

        main_layout.addWidget(self.table, stretch=1)

        # Bottom Action Bar
        bottom_row = QHBoxLayout()
        self.footer_lbl = QLabel(f"Audit Log: {self.audit_path}")
        self.footer_lbl.setStyleSheet(f"color: {C.MUTED_C}; font-size: 11px;")
        bottom_row.addWidget(self.footer_lbl)
        bottom_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        bottom_row.addWidget(close_btn)

        main_layout.addLayout(bottom_row)

    def refresh(self) -> None:
        """Reloads audit entries from disk and re-verifies cryptographic chain."""
        self._load_records()
        self._update_chain_status()
        self._apply_filter()

    def _load_records(self) -> None:
        self._records = []
        if not self.audit_path.exists():
            return

        try:
            with open(self.audit_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            self._records.append(json.loads(line))
                        except Exception:
                            pass
        except Exception as exc:
            logger.warning(f"[SecurityPanel] Failed to read audit log: {exc}")

        # Update stats
        total = len(self._records)
        approvals = sum(1 for r in self._records if "GRANT" in r.get("event", "") or "APPROV" in r.get("event", ""))
        denials = sum(1 for r in self._records if "DENI" in r.get("event", "") or "REJECT" in r.get("event", ""))
        violations = sum(1 for r in self._records if "VIOLAT" in r.get("event", "") or "TAMPER" in r.get("event", "") or "LOCKOUT" in r.get("event", ""))

        self.stat_total.setText(f"Total Events: {total}")
        self.stat_approvals.setText(f"Approvals: {approvals}")
        self.stat_denials.setText(f"Denials: {denials}")
        self.stat_violations.setText(f"Violations: {violations}")

    def _update_chain_status(self) -> None:
        ok, msg = verify_audit_log(self.audit_path)
        if ok:
            self.integrity_badge.setText("✓ Cryptographic Audit Chain Valid")
            self.integrity_badge.setStyleSheet(f"color: {C.GREEN};")
        else:
            self.integrity_badge.setText(f"✗ TAMPER DETECTED: {msg[:40]}...")
            self.integrity_badge.setStyleSheet(f"color: {C.RED};")

    def _apply_filter(self) -> None:
        mode = self.filter_combo.currentText()
        filtered: List[Dict[str, Any]] = []

        for r in self._records:
            ev = str(r.get("event", "")).upper()
            if mode == "All Events":
                filtered.append(r)
            elif mode == "Denials & Violations":
                if any(k in ev for k in ("DENI", "VIOLAT", "REJECT", "LOCKOUT", "TAMPER")):
                    filtered.append(r)
            elif mode == "Approvals & Grants":
                if any(k in ev for k in ("GRANT", "APPROV", "INSTALL", "VERIF")):
                    filtered.append(r)
            elif mode == "Secret Operations":
                if any(k in ev for k in ("SECRET", "TOKEN", "KEY")):
                    filtered.append(r)
            elif mode == "Gateway & Network":
                if any(k in ev for k in ("GATEWAY", "PAIR", "NETWORK", "SOCKET")):
                    filtered.append(r)

        self._render_table(filtered)

    def _render_table(self, records: List[Dict[str, Any]]) -> None:
        self.table.setRowCount(0)
        # Display most recent records at the top
        for row_idx, r in enumerate(reversed(records)):
            self.table.insertRow(row_idx)

            seq = str(r.get("sequence", ""))
            ts = str(r.get("timestamp", ""))[:19].replace("T", " ")
            event = str(r.get("event", ""))
            caller = str(r.get("caller", ""))
            details_dict = r.get("details", {})
            details_str = ", ".join(f"{k}={v}" for k, v in details_dict.items()) if details_dict else ""
            rec_hash = str(r.get("record_hash", ""))[:12]

            item_seq = QTableWidgetItem(seq)
            item_seq.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_ts = QTableWidgetItem(ts)
            item_ev = QTableWidgetItem(event)
            item_caller = QTableWidgetItem(caller)
            item_details = QTableWidgetItem(details_str)
            item_hash = QTableWidgetItem(rec_hash)
            item_hash.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # Color coding
            ev_upper = event.upper()
            if any(k in ev_upper for k in ("DENI", "VIOLAT", "REJECT", "LOCKOUT", "TAMPER")):
                item_ev.setForeground(QColor(C.RED))
            elif any(k in ev_upper for k in ("GRANT", "APPROV", "INSTALL", "VERIF")):
                item_ev.setForeground(QColor(C.GREEN))
            elif any(k in ev_upper for k in ("SECRET", "TOKEN")):
                item_ev.setForeground(QColor(C.ACC))

            for col_idx, item in enumerate([
                item_seq,
                item_ts,
                item_ev,
                item_caller,
                item_details,
                item_hash,
            ]):
                self.table.setItem(row_idx, col_idx, item)

    def _verify_chain_dialog(self) -> None:
        ok, msg = verify_audit_log(self.audit_path)
        dlg = QDialog(self)
        dlg.setWindowTitle("Audit Chain Integrity Verification")
        dlg.resize(500, 250)
        dlg.setStyleSheet(f"background-color: {C.PANEL}; color: {C.ICE};")
        vbox = QVBoxLayout(dlg)
        vbox.setContentsMargins(20, 20, 20, 20)

        header = QLabel("🔐 CRYPTOGRAPHIC AUDIT INTEGRITY")
        header.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        vbox.addWidget(header)

        status_lbl = QLabel(f"Chain Status: {'VALID & UNBROKEN' if ok else 'INTEGRITY COMPROMISED'}")
        status_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        status_lbl.setStyleSheet(f"color: {C.GREEN if ok else C.RED};")
        vbox.addWidget(status_lbl)

        detail_lbl = QLabel(msg)
        detail_lbl.setWordWrap(True)
        vbox.addWidget(detail_lbl)

        vbox.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(dlg.accept)
        vbox.addWidget(ok_btn, alignment=Qt.AlignmentFlag.AlignRight)
        dlg.exec()


def show_security_panel(parent: Optional[QWidget] = None, audit_path: Optional[Path] = None) -> None:
    """Convenience helper to display the security panel dialog."""
    dlg = SecurityPanelDialog(parent=parent, audit_path=audit_path)
    dlg.exec()
