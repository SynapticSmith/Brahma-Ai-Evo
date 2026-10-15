"""Atomic settings writes: the file must never be observable half-written."""

import json

import pytest

from core.user_paths import atomic_write_json


def _leftovers(directory, keep):
    return sorted(p.name for p in directory.iterdir() if p.name != keep)


def test_write_then_read_round_trip_and_no_temp_left(tmp_path):
    target = tmp_path / "config" / "app_settings.json"
    atomic_write_json(target, {"a": 1, "b": [1, 2, 3]})

    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "b": [1, 2, 3]}
    assert _leftovers(target.parent, target.name) == []


def test_overwrites_existing_file(tmp_path):
    target = tmp_path / "app_settings.json"
    target.write_text(json.dumps({"old": True}), encoding="utf-8")

    atomic_write_json(target, {"new": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    assert _leftovers(target.parent, target.name) == []


def test_unserializable_payload_leaves_target_untouched(tmp_path):
    target = tmp_path / "app_settings.json"
    target.write_text(json.dumps({"keep": "me"}), encoding="utf-8")

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": Unserializable()})

    assert json.loads(target.read_text(encoding="utf-8")) == {"keep": "me"}
    assert _leftovers(target.parent, target.name) == []


def test_config_manager_save_settings_is_atomic(tmp_path, monkeypatch):
    import memory.config_manager as cm

    target = tmp_path / "app_settings.json"
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cm, "SETTINGS_FILE", target)

    cm.save_settings({"x": 1})
    cm.save_settings({"y": 2})

    assert cm.load_settings() == {"x": 1, "y": 2}
    assert _leftovers(tmp_path, target.name) == []
