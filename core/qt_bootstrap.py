"""Qt / QtWebEngine runtime setup, in one explicit place.

The Chromium flags and the shared-OpenGL-context / vsync-off attributes were
previously set as import side effects duplicated across ``main``, ``ui`` and
``core.globe_window``; the copies could silently drift apart. They now live
here and are applied through :func:`bootstrap`, which every one of those modules
calls. Importing this module has no side effects of its own.
"""

from __future__ import annotations

import os
from typing import Final

# Hardware acceleration & WebGL flags for smooth 180fps+ rendering in Chromium.
QT_WEBENGINE_CHROMIUM_FLAGS: Final[str] = (
    "--enable-gpu-rasterization --enable-zero-copy --ignore-gpu-blocklist "
    "--enable-accelerated-2d-canvas --enable-webgl --enable-webgl2-compute-context "
    "--disable-frame-rate-limit --disable-gpu-vsync --num-raster-threads=4 "
    "--use-angle=d3d11 --disable-gpu-driver-bug-workarounds"
)


def configure_qt_environment() -> None:
    """Set the QtWebEngine Chromium flags. Idempotent; never overwrites an
    existing value so an operator-supplied override still wins."""
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", QT_WEBENGINE_CHROMIUM_FLAGS)


def configure_qt_attributes() -> None:
    """Request shared OpenGL contexts and an unthrottled swap interval. Must run
    before ``QApplication`` is constructed; safe to call again afterwards."""
    try:
        from PyQt6.QtCore import QCoreApplication, Qt
        from PyQt6.QtGui import QSurfaceFormat

        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
        fmt = QSurfaceFormat.defaultFormat()
        fmt.setSwapInterval(0)
        QSurfaceFormat.setDefaultFormat(fmt)
    except Exception:
        # A missing/partial PyQt6 install must not stop the app from importing.
        pass


def bootstrap() -> None:
    """Apply every Qt runtime setting. Idempotent, so any entry point may call it."""
    configure_qt_environment()
    configure_qt_attributes()
