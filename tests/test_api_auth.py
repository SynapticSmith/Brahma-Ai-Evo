"""Token registry: fail-closed presets, hash-only storage, revocation, reload."""

from __future__ import annotations

import json
import os

import pytest

from api_support import make_config
from brahma_api import auth
from brahma_api.auth import TokenRecord, TokenRegistry


def test_mint_needs_a_preset(tmp_path):
    config = make_config(tmp_path)
    registry = TokenRegistry(config.tokens_path)
    with pytest.raises(ValueError):
        registry.mint("", workspace="test")
    with pytest.raises(ValueError):
        registry.mint("superuser", workspace="test")


def test_reader_and_worker_need_a_workspace(tmp_path):
    registry = TokenRegistry(make_config(tmp_path).tokens_path)
    for preset in ("reader", "worker"):
        with pytest.raises(ValueError, match="needs a workspace"):
            registry.mint(preset)


def test_chat_has_no_workspace(tmp_path):
    registry = TokenRegistry(make_config(tmp_path).tokens_path)
    with pytest.raises(ValueError, match="no workspace"):
        registry.mint("chat", workspace="test")


def test_operator_requires_typed_confirmation(tmp_path):
    registry = TokenRegistry(make_config(tmp_path).tokens_path)
    with pytest.raises(ValueError, match="local shell access"):
        registry.mint("operator", workspace="test")
    record, token = registry.mint("operator", workspace="test", confirm="operator")
    assert record.profile == "operator"
    assert registry.verify(token).id == record.id


def test_unknown_scopes_are_rejected(tmp_path):
    registry = TokenRegistry(make_config(tmp_path).tokens_path)
    with pytest.raises(ValueError, match="unknown scopes"):
        registry.mint("reader", workspace="test", scopes=("chat", "root"))


def test_only_the_hash_is_written(tmp_path):
    config = make_config(tmp_path)
    registry = TokenRegistry(config.tokens_path)
    record, token = registry.mint("reader", workspace="test")
    raw = config.tokens_path.read_text(encoding="utf-8")
    assert token not in raw
    assert auth.hash_token(token) in raw
    payload = json.loads(raw)
    assert payload["tokens"][0]["sha256"] == record.sha256
    assert "profile" in payload["tokens"][0]


def test_a_record_without_a_profile_is_rejected_at_load(tmp_path):
    config = make_config(tmp_path)
    config.tokens_path.parent.mkdir(parents=True, exist_ok=True)
    config.tokens_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tokens": [
                    {"id": "tok_1", "sha256": "b" * 64, "workspace": "test"},
                    {"id": "tok_2", "sha256": "c" * 64, "profile": "reader", "workspace": "test"},
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = TokenRegistry(config.tokens_path)
    assert [record.id for record in registry.records()] == ["tok_2"]
    assert any("profile" in problem for problem in registry.problems)


def test_chat_record_with_a_workspace_is_rejected():
    with pytest.raises(ValueError, match="no workspace"):
        TokenRecord.from_dict(
            {"id": "tok", "sha256": "d" * 64, "profile": "chat", "workspace": "test"}
        )


def test_a_broken_file_does_not_raise(tmp_path):
    config = make_config(tmp_path)
    config.tokens_path.parent.mkdir(parents=True, exist_ok=True)
    config.tokens_path.write_text("{not json", encoding="utf-8")
    registry = TokenRegistry(config.tokens_path)
    assert registry.records() == []
    assert registry.problems


def test_revocation_is_immediate(tmp_path):
    registry = TokenRegistry(make_config(tmp_path).tokens_path)
    record, token = registry.mint("reader", workspace="test")
    assert registry.verify(token) is not None
    assert registry.revoke(record.id) is True
    assert registry.verify(token) is None
    assert registry.revoke("tok_missing") is False


def test_revocation_is_picked_up_by_a_running_registry(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    running = TokenRegistry(config.tokens_path)
    record, token = running.mint("reader", workspace="test")

    other = TokenRegistry(config.tokens_path)
    other.revoke(record.id)
    # Windows can hand two writes the same timestamp; make the change visible.
    stat = config.tokens_path.stat()
    os.utime(config.tokens_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    monkeypatch.setattr(auth, "RELOAD_INTERVAL_S", 0.0)
    running.maybe_reload()
    assert running.verify(token) is None


def test_a_new_key_is_picked_up_by_a_running_registry(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    running = TokenRegistry(config.tokens_path)
    other = TokenRegistry(config.tokens_path)
    record, token = other.mint("reader", workspace="test")

    monkeypatch.setattr(auth, "RELOAD_INTERVAL_S", 0.0)
    running.maybe_reload()
    assert running.verify(token).id == record.id


def test_touch_updates_memory_only(tmp_path):
    config = make_config(tmp_path)
    registry = TokenRegistry(config.tokens_path)
    record, _token = registry.mint("reader", workspace="test")
    before = config.tokens_path.read_text(encoding="utf-8")
    registry.touch(record.id)
    assert registry.records()[0].last_used_at is not None
    assert config.tokens_path.read_text(encoding="utf-8") == before
