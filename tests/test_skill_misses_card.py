"""The "Skill Routing Misses" card (design M5) and its read-only contract.

Two promises are easy to regress: the card must read the model-load stream and
show a miss, and it must never create the telemetry directory just by being
opened (the audit path is read-only).
"""
import os

import pytest


@pytest.fixture(scope="session")
def _app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture(scope="session")
def page(_app):
    import ui

    return ui.SystemConnectivityPage()


def _texts(card):
    from PyQt6.QtWidgets import QLabel

    return "\n".join(widget.text() for widget in card.findChildren(QLabel))


def test_card_shows_a_model_load_miss(page, monkeypatch, tmp_path):
    from agent.skill_audit import model_load_record
    from agent.trace import SkillLoadSink

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    sink = SkillLoadSink(base_dir=tmp_path / "BrahmaAI" / "skill_selections")
    sink.save(
        model_load_record(
            tool="load_project_skill",
            name="alpha",
            source="harness",
            injected="beta",
            indexed=True,
            now=1.0,
        )
    )

    text = _texts(page._build_skill_misses_card())

    assert "1 load(s)" in text
    assert "1 miss(es)" in text
    assert "alpha" in text
    assert "router injected beta" in text


def test_card_is_read_only_when_no_telemetry_exists(page, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    text = _texts(page._build_skill_misses_card())

    assert "No model-initiated skill loads recorded yet" in text
    assert not (tmp_path / "BrahmaAI").exists()
