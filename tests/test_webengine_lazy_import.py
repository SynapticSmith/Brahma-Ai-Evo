"""Regression tests for P3-20: QtWebEngine must not start on import.

Importing any of the UI/background/globe/HUD modules used to execute a
module-level ``import PyQt6.QtWebEngineWidgets`` (directly, or via an eager
``from ... import QWebEngineView``). That initialises QtWebEngine and spins up
Chromium's global threads, which race the Qt teardown at interpreter shutdown
and corrupt the heap -- so a fully green pytest run could still exit with
``0xC0000374`` (STATUS_HEAP_CORRUPTION) *after* ``N passed`` was printed.

The modules now probe availability with ``importlib.util.find_spec`` (which does
not execute the module) and import ``QWebEngineView`` lazily at first real use.

The import check runs in an isolated subprocess (offscreen) so it never inherits
the very teardown it guards against, mirroring
``test_ui_thread_marshalling.test_main_window_builds_in_subprocess``.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


def test_importing_ui_modules_does_not_start_webengine():
    root = Path(__file__).resolve().parent.parent
    script = textwrap.dedent(
        """
        import os, sys
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        sys.path.insert(0, sys.argv[1])
        from core.qt_bootstrap import bootstrap
        bootstrap()
        # ``import ui`` pulls in sound_manager (QSoundEffect), which needs a
        # live QApplication; create it first so the import cannot block.
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        app.setStyle("Fusion")

        import importlib.util as _u
        spec_ok = _u.find_spec("PyQt6.QtWebEngineWidgets") is not None

        import core.ui_bg
        import core.circuit_hud
        import core.globe_window
        import ui

        loaded = (
            "PyQt6.QtWebEngineWidgets" in sys.modules
            or "PyQt6.QtWebEngineCore" in sys.modules
        )
        assert not loaded, "QtWebEngine started on import (P3-20 regression)"

        # The feature is still offered: availability is probed without importing.
        assert core.ui_bg.WEB_ENGINE_AVAILABLE == spec_ok
        assert core.circuit_hud._WEBENGINE_OK == spec_ok

        sys.stdout.write("LAZY_OK\\n")
        sys.stdout.flush()
        os._exit(0)
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert "LAZY_OK" in proc.stdout, (
        f"QtWebEngine was imported eagerly (rc={proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr[-3000:]}"
    )


def test_webengine_view_still_builds_lazily():
    """Guard against 'fixing' P3-20 by disabling the view: the lazy helper must
    still construct a real QWebEngineView (and load WebEngine) on first use."""
    root = Path(__file__).resolve().parent.parent
    script = textwrap.dedent(
        """
        import os, sys
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        sys.path.insert(0, sys.argv[1])
        from core.qt_bootstrap import bootstrap
        bootstrap()
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)

        import core.ui_bg as b
        assert "PyQt6.QtWebEngineWidgets" not in sys.modules
        view = b._new_web_engine_view(None)
        assert view is not None, "lazy WebEngine view failed to build"
        assert "PyQt6.QtWebEngineWidgets" in sys.modules

        sys.stdout.write("VIEW_OK\\n")
        sys.stdout.flush()
        os._exit(0)
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert "VIEW_OK" in proc.stdout, (
        f"lazy WebEngine view did not build (rc={proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr[-3000:]}"
    )
