"""core/ui_openai_port.py — Settings Hub card for the OpenAI-Compatible API (Phase 2).

Provides the GUI card in System Connectivity for:
- Enabling / disabling the local OpenAI-compatible HTTP port (8010).
- Listing active API tokens with profile, scopes, and last used timestamp.
- Revoking tokens.
- Minting new bearer tokens with safe defaults (reader preselected, operator confirmation).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from brahma_api.auth import TokenRegistry
from brahma_api.config import BrahmaApiConfig

logger = logging.getLogger("UIOpenAIPort")


class OpenAIPortCard(QFrame):
    """Settings Hub card displaying OpenAI port status and active bearer tokens."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("OpenAIPortCard")
        self.setStyleSheet("""
            QFrame#OpenAIPortCard {
                background: rgba(10, 12, 18, 160);
                border: 1.5px solid rgba(0, 229, 255, 0.15);
                border-radius: 18px;
                padding: 16px;
            }
            QFrame#OpenAIPortCard:hover {
                border: 1.5px solid rgba(0, 229, 255, 0.35);
            }
            QPushButton {
                background: rgba(255, 255, 255, 0.05);
                color: #ffffff;
                border: 1px solid rgba(0, 229, 255, 0.3);
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 11px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255, 0.2);
                border: 1px solid rgba(0, 229, 255, 0.6);
            }
            QLineEdit, QComboBox {
                background: rgba(13, 15, 19, 240);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 8px;
                padding: 4px 8px;
                min-height: 28px;
            }
        """)

        self._base_dir = Path.cwd()
        self._init_ui()
        self.refresh()

    def _init_ui(self) -> None:
        self._main_lay = QVBoxLayout(self)
        self._main_lay.setContentsMargins(0, 0, 0, 0)
        self._main_lay.setSpacing(12)

        # Header row
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("OpenAI-Compatible Local API")
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffffff;")
        sub = QLabel("Local HTTP surface (default http://127.0.0.1:8010/v1) for Cline, Continue, and OpenAI SDK.")
        sub.setFont(QFont("Segoe UI", 9))
        sub.setStyleSheet("color: #8892b0;")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        header.addLayout(title_box)
        header.addStretch()

        self._main_lay.addLayout(header)

        # Status & Toggle row
        status_row = QHBoxLayout()
        self._status_lbl = QLabel("Status: Checking...")
        self._status_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        status_row.addWidget(self._status_lbl)
        status_row.addStretch()

        self._toggle_btn = QPushButton("Toggle")
        self._toggle_btn.clicked.connect(self._toggle_server)
        status_row.addWidget(self._toggle_btn)
        self._main_lay.addLayout(status_row)

        # Tokens list container
        tokens_header = QLabel("Active Bearer Tokens")
        tokens_header.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        tokens_header.setStyleSheet("color: #00e5ff; margin-top: 6px;")
        self._main_lay.addWidget(tokens_header)

        self._tokens_layout = QVBoxLayout()
        self._tokens_layout.setSpacing(6)
        self._main_lay.addLayout(self._tokens_layout)

        # Mint token section
        mint_box = QFrame()
        mint_box.setStyleSheet("background: rgba(0, 0, 0, 0.25); border-radius: 10px; padding: 10px;")
        mint_lay = QVBoxLayout(mint_box)
        mint_lay.setSpacing(8)

        mint_title = QLabel("Mint New Token")
        mint_title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        mint_title.setStyleSheet("color: #ffffff;")
        mint_lay.addWidget(mint_title)

        form_row = QHBoxLayout()
        form_row.addWidget(QLabel("Label:"))
        self._label_input = QLineEdit()
        self._label_input.setPlaceholderText("e.g. cline-laptop")
        form_row.addWidget(self._label_input, 2)

        form_row.addWidget(QLabel("Preset:"))
        self._preset_combo = QComboBox()
        self._preset_combo.addItems(["reader", "worker", "chat", "operator"])
        self._preset_combo.setCurrentText("reader")
        self._preset_combo.currentTextChanged.connect(self._on_preset_changed)
        form_row.addWidget(self._preset_combo, 1)

        mint_lay.addLayout(form_row)

        # Operator confirmation row (hidden by default)
        self._op_confirm_widget = QWidget()
        op_lay = QHBoxLayout(self._op_confirm_widget)
        op_lay.setContentsMargins(0, 0, 0, 0)
        op_warn = QLabel("⚠️ Operator profile grants local shell access. Type 'operator' to confirm:")
        op_warn.setStyleSheet("color: #ff5555; font-size: 10px;")
        op_lay.addWidget(op_warn)
        self._op_confirm_input = QLineEdit()
        self._op_confirm_input.setPlaceholderText("operator")
        op_lay.addWidget(self._op_confirm_input)
        self._op_confirm_widget.setVisible(False)
        mint_lay.addWidget(self._op_confirm_widget)

        # Mint button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._mint_btn = QPushButton("Mint Token")
        self._mint_btn.clicked.connect(self._mint_token)
        btn_row.addWidget(self._mint_btn)
        mint_lay.addLayout(btn_row)

        self._main_lay.addWidget(mint_box)

    def _on_preset_changed(self, preset: str) -> None:
        self._op_confirm_widget.setVisible(preset == "operator")

    def refresh(self) -> None:
        try:
            config = BrahmaApiConfig.load(self._base_dir)
            if config.enabled:
                self._status_lbl.setText(f"Status: Enabled (http://{config.host}:{config.port}/v1)")
                self._status_lbl.setStyleSheet("color: #00ff88; font-weight: bold;")
                self._toggle_btn.setText("Disable Port")
            else:
                self._status_lbl.setText("Status: Disabled (port 8010)")
                self._status_lbl.setStyleSheet("color: #8892b0; font-weight: bold;")
                self._toggle_btn.setText("Enable Port")

            # Clear existing token rows
            while self._tokens_layout.count():
                child = self._tokens_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            registry = TokenRegistry(config.tokens_path)
            active_tokens = [t for t in registry.list() if not t.revoked]
            if not active_tokens:
                no_tok = QLabel("No active tokens found.")
                no_tok.setStyleSheet("color: #8892b0; font-style: italic;")
                self._tokens_layout.addWidget(no_tok)
            else:
                for tok in active_tokens:
                    row = self._build_token_row(registry, tok)
                    self._tokens_layout.addWidget(row)

        except Exception as exc:
            logger.exception("Failed to refresh OpenAI port card: %s", exc)
            self._status_lbl.setText(f"Status: Error loading config ({exc})")
            self._status_lbl.setStyleSheet("color: #ff5555;")

    def _build_token_row(self, registry: TokenRegistry, tok: Any) -> QWidget:
        row = QFrame()
        row.setStyleSheet("background: rgba(255, 255, 255, 0.03); border-radius: 8px; padding: 6px 10px;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(6, 4, 6, 4)

        info = QVBoxLayout()
        name_line = QHBoxLayout()
        lbl = QLabel(f"<b>{tok.label or 'Unnamed'}</b> ({tok.id})")
        lbl.setStyleSheet("color: #ffffff;")
        name_line.addWidget(lbl)

        preset_badge = QLabel(f"[{tok.profile}]")
        if tok.profile == "operator":
            preset_badge.setStyleSheet("color: #ff5555; font-weight: bold;")
        elif tok.profile == "worker":
            preset_badge.setStyleSheet("color: #ffbb00; font-weight: bold;")
        else:
            preset_badge.setStyleSheet("color: #00e5ff; font-weight: bold;")
        name_line.addWidget(preset_badge)
        name_line.addStretch()
        info.addLayout(name_line)

        scopes_text = ", ".join(tok.scopes)
        details = QLabel(f"Scopes: {scopes_text} | Created: {tok.created_at[:10]}")
        details.setStyleSheet("color: #8892b0; font-size: 10px;")
        info.addWidget(details)

        lay.addLayout(info, 1)

        revoke_btn = QPushButton("Revoke")
        revoke_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 60, 60, 0.15);
                color: #ff6666;
                border: 1px solid rgba(255, 60, 60, 0.3);
            }
            QPushButton:hover {
                background: rgba(255, 60, 60, 0.35);
                border: 1px solid rgba(255, 60, 60, 0.6);
            }
        """)
        revoke_btn.clicked.connect(lambda _, tid=tok.id: self._revoke_token(registry, tid))
        lay.addWidget(revoke_btn)

        return row

    def _toggle_server(self) -> None:
        try:
            config = BrahmaApiConfig.load(self._base_dir)
            new_config = BrahmaApiConfig(
                base_dir=config.base_dir,
                enabled=not config.enabled,
                host=config.host,
                port=config.port,
                allow_remote_bind=config.allow_remote_bind,
                auth_required=config.auth_required,
                preselect_profile=config.preselect_profile,
                default_provider=config.default_provider,
                workspaces=config.workspaces,
                limits=config.limits,
                stream_mode=config.stream_mode,
                keepalive_s=config.keepalive_s,
                audit=config.audit,
                audit_log=config.audit_log,
            )
            new_config.save()
            self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Config Error", f"Failed to save settings: {exc}")

    def _revoke_token(self, registry: TokenRegistry, token_id: str) -> None:
        try:
            ok = registry.revoke(token_id)
            if ok:
                self.refresh()
            else:
                QMessageBox.warning(self, "Revocation Failed", f"Token {token_id} not found.")
        except Exception as exc:
            QMessageBox.critical(self, "Revocation Error", str(exc))

    def _mint_token(self) -> None:
        label = self._label_input.text().strip()
        preset = self._preset_combo.currentText().strip()
        confirm = self._op_confirm_input.text().strip() if preset == "operator" else None

        if preset == "operator" and confirm != "operator":
            QMessageBox.warning(
                self,
                "Confirmation Required",
                "The operator preset grants local shell access. You must type 'operator' to confirm.",
            )
            return

        try:
            config = BrahmaApiConfig.load(self._base_dir)
            registry = TokenRegistry(config.tokens_path)
            workspace = next(iter(config.workspaces), None) if preset != "chat" else None

            record, plaintext = registry.mint(
                preset=preset,
                label=label or f"{preset}-token",
                workspace=workspace,
                confirm=confirm,
            )
            self._label_input.clear()
            self._op_confirm_input.clear()
            self.refresh()

            # Show plaintext bearer token once
            QMessageBox.information(
                self,
                "Token Created",
                f"Bearer token minted for '{record.label}':\n\n{plaintext}\n\n"
                "Save this token now. It will NEVER be shown again.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Minting Error", str(exc))


def build_openai_port_card(parent: Optional[QWidget] = None) -> QFrame:
    """Entry point for embedding in SystemConnectivityPage."""
    return OpenAIPortCard(parent)
