"""Qt / QtWebEngine runtime setup, in one explicit place.

The Chromium flags, the shared-OpenGL-context / vsync-off attributes and the
process DPI-awareness context were previously set as import side effects
duplicated across ``main``, ``ui`` and ``core.globe_window``; the copies could
silently drift apart. They now live here and are applied through
:func:`bootstrap`, which every one of those modules calls. Importing this module
has no side effects of its own.
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


def configure_dpi_awareness() -> None:
    """Fix the process DPI-awareness context to per-monitor-v2 before
    ``QApplication`` exists.

    Qt's Windows platform plugin sets ``PER_MONITOR_AWARE_V2`` while
    constructing ``QGuiApplication``. If something already fixed the process to
    a *different* context first -- a manifest, or a helper library such as
    pywinauto / mss imported earlier in the startup path -- Qt's own
    ``SetProcessDpiAwarenessContext()`` call fails with ACCESS_DENIED and the
    plugin logs ``SetProcessDpiAwarenessContext() failed: Access is denied.``
    Setting the same V2 context here means Qt's call becomes an idempotent
    no-op, so the notice cannot appear. This matches Qt's default, so there is
    no behaviour change.

    Idempotent and silent on failure. Set ``BRAHMA_DPI_AWARENESS=off`` to skip
    (to defer to a ``qt.conf``/manifest instead)."""
    if os.name != "nt":
        return
    if os.environ.get("BRAHMA_DPI_AWARENESS", "").strip().lower() in {
        "0",
        "off",
        "none",
        "skip",
    }:
        return
    try:
        import ctypes

        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is the pseudo-handle -4
        # (windef.h). ctypes must pass a pointer-width handle, so set argtypes.
        set_ctx = ctypes.windll.user32.SetProcessDpiAwarenessContext
        set_ctx.argtypes = [ctypes.c_void_p]
        set_ctx.restype = ctypes.c_bool
        set_ctx(ctypes.c_void_p(-4))
    except Exception:
        # Pre-1607 Windows or a hardened environment: leave awareness to Qt.
        pass


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
    configure_dpi_awareness()
    configure_qt_attributes()
