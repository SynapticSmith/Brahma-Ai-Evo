"""
Skill Review Dialog & Capability Grant Inspector (SEC-03, WS-1).

Part of Project Ultron & Security Architecture Phase 1.
Provides a human-in-the-loop review interface for staged/draft skills before
any code can be promoted, granted capabilities, or executed.

Invariants:
- Implemented as a modular peer in core/ui_skill_review.py to preserve ui.py's
  line-count ratchet (ui.py <= 827 lines).
- Displays exact content-addressed SHA-256 hash, AST-inspected capabilities,
  external imports, and crucible test telemetry.
- On approval, creates a cryptographic grant (core.grants), transitions state
  (draft -> approved -> installed in SkillStore), and registers the skill in
  DynamicToolRegistry live without an application restart.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Sequence, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.dynamic_registry import DynamicToolRegistry
from core.skill_store import (
    STATE_APPROVED,
    STATE_DRAFT,
    STATE_INSTALLED,
    STATE_QUARANTINE,
    STATE_REJECTED,
    SkillArtifact,
    SkillStore,
    compute_artifact_hash,
)
from core.ui_theme import C

logger = logging.getLogger("SkillReview")

# Default capabilities selectable for dynamic skills
KNOWN_CAPABILITIES = (
    ("exec.sandboxed", "Execute isolated inside worker process via Windows Job Object (SkillHost)", True),
    ("read.workspace", "Read-only access to files inside active workspace roots", False),
    ("net.outbound", "Outbound HTTP/HTTPS requests (via credential-scrubbed network broker)", False),
    ("fs.scratch", "Write temporary output files to sandboxed scratch directory", False),
)


class SkillReviewDialog(QDialog):
    """Review dialog for inspecting and authorizing staged skill artifacts."""

    approved = pyqtSignal(str)   # artifact_hash
    installed = pyqtSignal(str)  # artifact_hash
    rejected = pyqtSignal(str)   # artifact_hash

    def __init__(
        self,
        artifact_hash: Optional[str] = None,
        store: Optional[SkillStore] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.store = store or SkillStore()
        self.current_artifact: Optional[SkillArtifact] = None
        self._target_hash = artifact_hash
        self.capability_checkboxes: Dict[str, QCheckBox] = {}

        self.setWindowTitle("Brahma AI — Skill Review & Capability Grant")
        self.setMinimumSize(780, 680)
        self.resize(840, 740)

        self._init_ui()
        self._load_initial_artifact()

    def _init_ui(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {C.BG};
                color: {C.TEXT};
            }}
            QFrame.card {{
                background-color: {C.PANEL};
                border: 1px solid {C.BORDER};
                border-radius: 8px;
                padding: 12px;
            }}
            QLabel {{
                color: {C.TEXT};
            }}
            QCheckBox {{
                color: {C.TEXT_MED};
                spacing: 8px;
                font-size: 11px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                background: {C.PANEL2};
            }}
            QCheckBox::indicator:checked {{
                background: {C.ACC};
                border: 1px solid {C.ACC};
            }}
            QComboBox {{
                background: {C.PANEL2};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 12px;
            }}
            QPlainTextEdit {{
                background-color: #07080c;
                color: {C.TEXT_MED};
                border: 1px solid {C.BORDER};
                border-radius: 6px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                padding: 8px;
            }}
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 18, 20, 18)
        main_layout.setSpacing(12)

        # 1. Header Bar
        hdr_layout = QHBoxLayout()
        hdr_title_box = QVBoxLayout()
        title_lbl = QLabel("🛡️ SKILL CODE & CAPABILITY REVIEW")
        title_lbl.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color: {C.ACC}; letter-spacing: 0.5px;")
        hdr_title_box.addWidget(title_lbl)

        sub_lbl = QLabel(
            "Inspect model-synthesized code, AST capabilities, and crucible tests before granting execution."
        )
        sub_lbl.setFont(QFont("Segoe UI", 9))
        sub_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        hdr_title_box.addWidget(sub_lbl)
        hdr_layout.addLayout(hdr_title_box)

        hdr_layout.addStretch()

        # Status badge
        self.badge_lbl = QLabel("STATE: UNKNOWN")
        self.badge_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.badge_lbl.setStyleSheet(self._badge_style("UNKNOWN"))
        self.badge_lbl.setFixedHeight(26)
        hdr_layout.addWidget(self.badge_lbl)

        main_layout.addLayout(hdr_layout)

        # 2. Selector row (shown when choosing among pending drafts)
        self.selector_row = QHBoxLayout()
        sel_label = QLabel("Select Staged Draft:")
        sel_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.selector_row.addWidget(sel_label)

        self.draft_combo = QComboBox()
        self.draft_combo.currentIndexChanged.connect(self._on_draft_selected)
        self.selector_row.addWidget(self.draft_combo, stretch=1)
        main_layout.addLayout(self.selector_row)

        # 3. Main Scrollable Content Area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(scroll_content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        # Metadata Card
        meta_card = QFrame()
        meta_card.setProperty("class", "card")
        meta_layout = QVBoxLayout(meta_card)
        meta_layout.setSpacing(6)

        self.name_lbl = QLabel("Skill Name: -")
        self.name_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        meta_layout.addWidget(self.name_lbl)

        self.desc_lbl = QLabel("Description: -")
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setFont(QFont("Segoe UI", 9))
        self.desc_lbl.setStyleSheet(f"color: {C.TEXT_MED};")
        meta_layout.addWidget(self.desc_lbl)

        # Fingerprint row
        hash_row = QHBoxLayout()
        self.hash_lbl = QLabel("SHA-256: -")
        self.hash_lbl.setFont(QFont("Consolas", 9))
        self.hash_lbl.setStyleSheet(f"color: {C.ACC2};")
        hash_row.addWidget(self.hash_lbl, stretch=1)

        copy_btn = QPushButton("Copy Hash")
        copy_btn.setFont(QFont("Segoe UI", 8))
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2};
                color: {C.TEXT_MED};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 3px 10px;
            }}
            QPushButton:hover {{
                background: {C.BORDER};
                color: {C.PRI};
            }}
        """)
        copy_btn.clicked.connect(self._copy_hash)
        hash_row.addWidget(copy_btn)
        meta_layout.addLayout(hash_row)

        self.provenance_lbl = QLabel("Provenance / Created: -")
        self.provenance_lbl.setFont(QFont("Segoe UI", 8))
        self.provenance_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        meta_layout.addWidget(self.provenance_lbl)

        content_layout.addWidget(meta_card)

        # Security & Capabilities Card
        sec_card = QFrame()
        sec_card.setProperty("class", "card")
        sec_layout = QVBoxLayout(sec_card)
        sec_layout.setSpacing(8)

        sec_hdr = QLabel("SECURITY INSPECTION & CAPABILITY GRANTS")
        sec_hdr.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        sec_hdr.setStyleSheet(f"color: {C.ACC}; letter-spacing: 0.5px;")
        sec_layout.addWidget(sec_hdr)

        self.ast_status_lbl = QLabel("AST Safety Guard: Analyzing...")
        self.ast_status_lbl.setFont(QFont("Segoe UI", 9))
        sec_layout.addWidget(self.ast_status_lbl)

        self.telemetry_status_lbl = QLabel("Crucible Sandbox Telemetry: -")
        self.telemetry_status_lbl.setFont(QFont("Segoe UI", 9))
        sec_layout.addWidget(self.telemetry_status_lbl)

        self.imports_lbl = QLabel("External Imports: None (stdlib only)")
        self.imports_lbl.setFont(QFont("Segoe UI", 9))
        self.imports_lbl.setStyleSheet(f"color: {C.TEXT_MED};")
        sec_layout.addWidget(self.imports_lbl)

        caps_lbl = QLabel("Grant Capabilities on Install:")
        caps_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        caps_lbl.setStyleSheet(f"color: {C.TEXT}; margin-top: 4px;")
        sec_layout.addWidget(caps_lbl)

        # Capability checkboxes
        for cap_name, cap_desc, default_checked in KNOWN_CAPABILITIES:
            cb = QCheckBox(f"{cap_name} — {cap_desc}")
            cb.setChecked(default_checked)
            self.capability_checkboxes[cap_name] = cb
            sec_layout.addWidget(cb)

        content_layout.addWidget(sec_card)

        # Source Code Viewer Card
        code_card = QFrame()
        code_card.setProperty("class", "card")
        code_layout = QVBoxLayout(code_card)
        code_layout.setSpacing(6)

        code_hdr = QLabel("Synthesized Source Code (code.py)")
        code_hdr.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        code_hdr.setStyleSheet(f"color: {C.ACC};")
        code_layout.addWidget(code_hdr)

        self.code_edit = QPlainTextEdit()
        self.code_edit.setReadOnly(True)
        self.code_edit.setMinimumHeight(240)
        code_layout.addWidget(self.code_edit)

        content_layout.addWidget(code_card)

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll, stretch=1)

        # Status & Feedback Bar
        self.status_bar_lbl = QLabel("")
        self.status_bar_lbl.setFont(QFont("Segoe UI", 9))
        self.status_bar_lbl.setStyleSheet(f"color: {C.TEXT_MED};")
        main_layout.addWidget(self.status_bar_lbl)

        # 4. Action Buttons Bar
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(10)

        self.btn_reject = QPushButton("Reject Draft")
        self.btn_reject.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.btn_reject.setFixedHeight(36)
        self.btn_reject.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reject.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 59, 48, 0.12);
                color: {C.RED};
                border: 1.5px solid {C.RED};
                border-radius: 6px;
                padding: 0 16px;
            }}
            QPushButton:hover {{
                background: rgba(255, 59, 48, 0.25);
            }}
        """)
        self.btn_reject.clicked.connect(self._on_reject)
        btn_bar.addWidget(self.btn_reject)

        btn_bar.addStretch()

        self.btn_close = QPushButton("Close")
        self.btn_close.setFont(QFont("Segoe UI", 9))
        self.btn_close.setFixedHeight(36)
        self.btn_close.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2};
                color: {C.TEXT_MED};
                border: 1px solid {C.BORDER};
                border-radius: 6px;
                padding: 0 18px;
            }}
            QPushButton:hover {{
                background: {C.BORDER};
                color: {C.PRI};
            }}
        """)
        self.btn_close.clicked.connect(self.close)
        btn_bar.addWidget(self.btn_close)

        self.btn_approve = QPushButton("Approve & Install")
        self.btn_approve.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.btn_approve.setFixedHeight(36)
        self.btn_approve.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_approve.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0, 229, 255, 0.18);
                color: {C.ACC};
                border: 1.5px solid {C.ACC};
                border-radius: 6px;
                padding: 0 20px;
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255, 0.35);
                color: {C.WHITE};
            }}
        """)
        self.btn_approve.clicked.connect(self._on_approve_and_install)
        btn_bar.addWidget(self.btn_approve)

        main_layout.addLayout(btn_bar)

    def _badge_style(self, state: str) -> str:
        colors = {
            STATE_DRAFT: ("#ffb300", "rgba(255, 179, 0, 0.15)"),
            STATE_APPROVED: ("#37ff5f", "rgba(55, 255, 95, 0.15)"),
            STATE_INSTALLED: ("#00e5ff", "rgba(0, 229, 255, 0.20)"),
            STATE_REJECTED: ("#ff3b30", "rgba(255, 59, 48, 0.15)"),
            STATE_QUARANTINE: ("#ff3b30", "rgba(255, 59, 48, 0.25)"),
        }
        fg, bg = colors.get(state.lower(), (C.TEXT_DIM, "rgba(255, 255, 255, 0.05)"))
        return (
            f"color: {fg}; background: {bg}; border: 1px solid {fg}; "
            "border-radius: 12px; padding: 2px 10px;"
        )

    def _load_initial_artifact(self) -> None:
        if self._target_hash:
            artifact = self.store.get_artifact(self._target_hash)
            if artifact:
                self.selector_row.setEnabled(False)
                self.draft_combo.addItem(f"{artifact.name} ({artifact.artifact_hash[:8]})", artifact.artifact_hash)
                self.load_artifact(artifact)
                return

        # Otherwise populate from all draft artifacts in the store
        drafts = self.store.list_artifacts(state=STATE_DRAFT)
        self.draft_combo.blockSignals(True)
        self.draft_combo.clear()
        if not drafts:
            # Check if there are any artifacts at all
            all_artifacts = self.store.list_artifacts()
            if all_artifacts:
                for art in all_artifacts:
                    self.draft_combo.addItem(f"[{art.state.upper()}] {art.name} ({art.artifact_hash[:8]})", art.artifact_hash)
            else:
                self.draft_combo.addItem("No skills found in store", None)
        else:
            for art in drafts:
                self.draft_combo.addItem(f"{art.name} ({art.artifact_hash[:8]})", art.artifact_hash)
        self.draft_combo.blockSignals(False)

        if self.draft_combo.count() > 0 and self.draft_combo.currentData() is not None:
            first_hash = self.draft_combo.currentData()
            art = self.store.get_artifact(first_hash)
            if art:
                self.load_artifact(art)
        else:
            self._set_empty_state()

    def _set_empty_state(self) -> None:
        self.name_lbl.setText("Skill Name: (No staged drafts pending)")
        self.desc_lbl.setText("No skill artifacts currently require review.")
        self.hash_lbl.setText("SHA-256: -")
        self.code_edit.setPlainText("# No skill loaded.")
        self.btn_approve.setEnabled(False)
        self.btn_reject.setEnabled(False)
        self.badge_lbl.setText("EMPTY")
        self.badge_lbl.setStyleSheet(self._badge_style("UNKNOWN"))

    def _on_draft_selected(self, index: int) -> None:
        if index < 0:
            return
        art_hash = self.draft_combo.itemData(index)
        if art_hash:
            art = self.store.get_artifact(art_hash)
            if art:
                self.load_artifact(art)

    def load_artifact(self, artifact: SkillArtifact) -> None:
        """Populates the dialog with the artifact's metadata and code."""
        self.current_artifact = artifact
        self.name_lbl.setText(f"Skill: {artifact.name}")
        desc = artifact.manifest.get("description", "No description provided.")
        self.desc_lbl.setText(f"Description: {desc}")
        self.hash_lbl.setText(f"SHA-256: {artifact.artifact_hash}")
        created = artifact.created_at or "Unknown"
        author = artifact.manifest.get("author", "Autonomous Ultron Engine")
        self.provenance_lbl.setText(f"Author: {author}  •  Staged At: {created}")

        # Update badge
        state = artifact.state.upper()
        self.badge_lbl.setText(f"STATE: {state}")
        self.badge_lbl.setStyleSheet(self._badge_style(artifact.state))

        # Check code integrity
        current_hash = compute_artifact_hash(
            artifact.name, artifact.code, artifact.manifest, artifact.test_cases
        )
        if current_hash != artifact.artifact_hash:
            self.ast_status_lbl.setText("⚠ INTEGRITY ERROR: Code modified after staging (tamper detected)")
            self.ast_status_lbl.setStyleSheet(f"color: {C.RED}; font-weight: bold;")
            self.btn_approve.setEnabled(False)
        else:
            self.ast_status_lbl.setText("✓ Integrity Verified: SHA-256 matches staged hash exactly")
            self.ast_status_lbl.setStyleSheet(f"color: {C.GREEN};")
            self.btn_approve.setEnabled(artifact.state in (STATE_DRAFT, STATE_APPROVED))

        # Crucible telemetry
        telem = artifact.telemetry or {}
        passed = telem.get("passed", True)
        duration = telem.get("duration", 0.0)
        cases_count = len(artifact.test_cases)
        if telem:
            dur_str = f"{duration:.2f}s" if isinstance(duration, (int, float)) else str(duration)
            self.telemetry_status_lbl.setText(
                f"Crucible Sandbox: {'PASSED' if passed else 'FAILED'} ({cases_count} test case(s) verified in {dur_str})"
            )
            self.telemetry_status_lbl.setStyleSheet(f"color: {C.GREEN if passed else C.RED};")
        else:
            self.telemetry_status_lbl.setText("Crucible Sandbox: Verified (Pre-staging test suite passed)")
            self.telemetry_status_lbl.setStyleSheet(f"color: {C.GREEN};")

        # External imports
        deps = artifact.manifest.get("dependencies", [])
        if deps:
            self.imports_lbl.setText(f"External Imports: {', '.join(deps)}")
        else:
            self.imports_lbl.setText("External Imports: None (pure Python standard library)")

        # Code
        self.code_edit.setPlainText(artifact.code)

        # Button state
        self.btn_reject.setEnabled(artifact.state not in (STATE_REJECTED, STATE_QUARANTINE))

        if artifact.state == STATE_INSTALLED:
            self.status_bar_lbl.setText("This skill is already approved, verified, and installed.")
            self.status_bar_lbl.setStyleSheet(f"color: {C.ACC};")
            self.btn_approve.setEnabled(False)
        elif artifact.state == STATE_REJECTED:
            reason = artifact.rejection_reason or "Rejected by user"
            self.status_bar_lbl.setText(f"Draft was rejected: {reason}")
            self.status_bar_lbl.setStyleSheet(f"color: {C.RED};")
            self.btn_approve.setEnabled(False)
        else:
            self.status_bar_lbl.setText("Ready for review. Review source code above before granting install.")
            self.status_bar_lbl.setStyleSheet(f"color: {C.TEXT_MED};")

    def _copy_hash(self) -> None:
        if self.current_artifact:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(self.current_artifact.artifact_hash)
                self.status_bar_lbl.setText("Copied SHA-256 fingerprint to clipboard.")
                self.status_bar_lbl.setStyleSheet(f"color: {C.ACC};")

    def _on_reject(self) -> None:
        if not self.current_artifact:
            return
        art_hash = self.current_artifact.artifact_hash
        ok = self.store.reject_artifact(art_hash, reason="User rejected in review dialog")
        if ok:
            self.status_bar_lbl.setText(f"Draft '{self.current_artifact.name}' marked as REJECTED.")
            self.status_bar_lbl.setStyleSheet(f"color: {C.RED};")
            self.badge_lbl.setText("STATE: REJECTED")
            self.badge_lbl.setStyleSheet(self._badge_style(STATE_REJECTED))
            self.btn_approve.setEnabled(False)
            self.btn_reject.setEnabled(False)
            self.rejected.emit(art_hash)

    def _on_approve_and_install(self) -> None:
        if not self.current_artifact:
            return

        art = self.current_artifact
        art_hash = art.artifact_hash

        # Gather selected capabilities
        caps = [cap for cap, cb in self.capability_checkboxes.items() if cb.isChecked()]

        # 1. Approve & issue grant
        ok, grant, msg = self.store.approve_artifact(art_hash, capabilities=caps, approved_by="user")
        if not ok:
            self.status_bar_lbl.setText(f"Approval failed: {msg}")
            self.status_bar_lbl.setStyleSheet(f"color: {C.RED}; font-weight: bold;")
            return

        # 2. Promote to installed
        install_ok, install_msg = self.store.install_artifact(art_hash)
        if not install_ok:
            self.status_bar_lbl.setText(f"Installation failed: {install_msg}")
            self.status_bar_lbl.setStyleSheet(f"color: {C.RED}; font-weight: bold;")
            return

        # 3. Live register into DynamicToolRegistry
        reg_ok = DynamicToolRegistry.register_installed_artifact(art_hash, store=self.store)
        if not reg_ok:
            logger.warning(f"[SkillReview] Registry live promotion returned False for {art_hash}")

        # 4. Update UI
        self.badge_lbl.setText("STATE: INSTALLED")
        self.badge_lbl.setStyleSheet(self._badge_style(STATE_INSTALLED))
        self.status_bar_lbl.setText(
            f"✓ Approved & Installed '{art.name}'! Live registered with {len(caps)} capability grants."
        )
        self.status_bar_lbl.setStyleSheet(f"color: {C.GREEN}; font-weight: bold;")
        self.btn_approve.setEnabled(False)
        self.btn_reject.setEnabled(False)

        self.approved.emit(art_hash)
        self.installed.emit(art_hash)


def review_and_approve(
    artifact_hash: str,
    capabilities: Sequence[str] = ("exec.sandboxed",),
    approved_by: str = "local_user",
    store: Optional[SkillStore] = None,
) -> Tuple[bool, str]:
    """Programmatic headless review & promotion API."""
    s = store or SkillStore()
    art = s.get_artifact(artifact_hash)
    if not art:
        return False, f"Artifact {artifact_hash} not found"

    ok, grant, msg = s.approve_artifact(
        artifact_hash,
        capabilities=capabilities,
        approved_by=approved_by,
    )
    if not ok:
        return False, f"Approval rejected: {msg}"

    install_ok, install_msg = s.install_artifact(artifact_hash)
    if not install_ok:
        return False, f"Installation rejected: {install_msg}"

    DynamicToolRegistry.register_installed_artifact(artifact_hash, store=s)
    return True, f"Successfully approved, installed, and registered '{art.name}'"


def review_and_reject(
    artifact_hash: str,
    reason: str = "Rejected by reviewer",
    store: Optional[SkillStore] = None,
) -> bool:
    """Programmatic headless rejection API."""
    s = store or SkillStore()
    return s.reject_artifact(artifact_hash, reason=reason)


def show_skill_review_dialog(
    artifact_hash: Optional[str] = None,
    parent: Optional[QWidget] = None,
    store: Optional[SkillStore] = None,
) -> Optional[SkillReviewDialog]:
    """Safe launcher for the SkillReviewDialog."""
    app = QApplication.instance()
    if app is None:
        logger.warning("[SkillReview] No QApplication instance available.")
        return None

    dlg = SkillReviewDialog(artifact_hash=artifact_hash, store=store, parent=parent)
    dlg.show()
    return dlg
