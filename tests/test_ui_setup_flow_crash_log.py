"""Regression test for the setup crash log destination (WP07-12)."""

from pathlib import Path

import pytest

from core import ui_setup_flow, user_paths


def test_setup_failure_writes_crash_log_to_user_data_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    user_data = tmp_path / "userdata"
    monkeypatch.setattr(user_paths, "get_user_data_dir", lambda: user_data)

    def failing_overlay(*args, **kwargs):
        raise RuntimeError("setup exploded")

    monkeypatch.setattr(ui_setup_flow, "SetupOverlay", failing_overlay, raising=False)

    class CentralWidget:
        def width(self):
            return 800

        def height(self):
            return 600

    class Host(ui_setup_flow._SetupFlowMixin):
        _overlay = None

        def __init__(self):
            self._central = CentralWidget()

        def showMaximized(self):
            return None

        def centralWidget(self):
            return self._central

        def _load_api_defaults(self):
            return {}

    with pytest.raises(RuntimeError, match="setup exploded"):
        Host()._show_setup()

    crash_log = user_data / "logs" / "setup_crash.log"
    assert crash_log.is_file()
    assert "RuntimeError: setup exploded" in crash_log.read_text(encoding="utf-8")
    assert not (Path.cwd() / "setup_crash.log").exists()
