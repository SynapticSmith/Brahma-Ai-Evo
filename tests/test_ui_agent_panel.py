"""Tests for the Agent & Orchestrator Bridge UI Card (core/ui_agent_panel.py)."""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from core.agent_bridge.bridge import get_agent_bridge, close_agent_bridge
from core.agent_bridge.models import AgentRecord
from core.agent_bridge.registry import AgentRegistry
from core.ui_agent_panel import AgentBridgeCard, build_agent_bridge_card


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def clean_bridge(tmp_path: Path):
    close_agent_bridge()
    reg = AgentRegistry(store_path=tmp_path / "registry.json")
    bridge = get_agent_bridge(registry=reg, artifacts_dir=tmp_path / "artifacts")
    yield bridge
    close_agent_bridge()


def test_agent_bridge_card_empty_state(qapp, clean_bridge):
    card = build_agent_bridge_card()
    assert isinstance(card, AgentBridgeCard)
    assert "0 agent(s) registered" in card._summary_lbl.text() or "0 agents" in card._summary_lbl.text()
    card.deleteLater()


def test_agent_bridge_card_with_records(qapp, clean_bridge):
    clean_bridge.registry.save(
        AgentRecord(
            id="ollama",
            display_name="Ollama Server",
            kind="local_model",
            endpoint="http://localhost:11434",
            status="connected",
            enabled=True,
        )
    )
    clean_bridge.registry.save(
        AgentRecord(
            id="claude_code",
            display_name="Claude Code",
            kind="cli_agent",
            executable="claude.exe",
            status="found",
            enabled=False,
        )
    )

    card = build_agent_bridge_card()
    card.refresh_records()

    assert "2 agent(s) registered" in card._summary_lbl.text()
    assert "1 enabled" in card._summary_lbl.text()

    # Verify toggle modifies registry state
    card._on_toggle_agent("claude_code", True)
    rec = clean_bridge.registry.get("claude_code")
    assert rec is not None
    assert rec.enabled is True
    assert "2 enabled" in card._summary_lbl.text()

    card.deleteLater()
