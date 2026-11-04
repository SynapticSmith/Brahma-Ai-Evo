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
import sys
import warnings
from typing import Final

# Hardware acceleration & WebGL flags for smooth 180fps+ rendering in Chromium.
QT_WEBENGINE_CHROMIUM_FLAGS: Final[str] = (
    "--enable-gpu-rasterization --enable-zero-copy --ignore-gpu-blocklist "
    "--enable-accelerated-2d-canvas --enable-webgl --enable-webgl2-compute-context "
    "--disable-frame-rate-limit --disable-gpu-vsync --num-raster-threads=4 "
    "--use-angle=d3d11 --disable-gpu-driver-bug-workarounds"
)


def ensure_supported_environment() -> None:
    """Validate that the active Python interpreter has required GUI/system
    dependencies. If running under an incompatible runtime (such as Python 3.14
    preview without installed packages) while a supported Python (3.11/3.12) is
    available on the machine, transparently relaunch using that runtime."""
    if os.environ.get("BRAHMA_NO_RELAUNCH"):
        return

    # Bypass relaunch check during unit tests or non-script execution
    if "pytest" in sys.modules or (sys.argv and "pytest" in sys.argv[0]):
        return
    if not sys.argv or sys.argv[0] == "-c":
        return

    try:
        import PyQt6  # noqa: F401
        return
    except ImportError:
        pass

    import subprocess
    from pathlib import Path

    workspace_root = Path(__file__).resolve().parent.parent
    venv_py = workspace_root / ".venv" / "Scripts" / "python.exe"

    candidates: list[list[str]] = []
    if venv_py.exists():
        candidates.append([str(venv_py)])
    if sys.platform == "win32":
        candidates.extend([["py", "-3.11"], ["py", "-3.12"], ["py", "-3"]])
    else:
        candidates.extend([["python3.11"], ["python3.12"], ["python3"]])

    for cmd in candidates:
        try:
            probe = subprocess.run(
                [*cmd, "-c", "import PyQt6"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if probe.returncode == 0:
                print(
                    f"[Brahma AI] Incompatible Python runtime (Python {sys.version.split()[0]} at {sys.executable}).",
                    file=sys.stderr,
                )
                print(
                    f"[Brahma AI] Forwarding launch to compatible Python runtime: {' '.join(cmd)}...",
                    file=sys.stderr,
                )
                full_cmd = [*cmd, *sys.argv]
                try:
                    rc = subprocess.call(full_cmd)
                    sys.exit(rc)
                except KeyboardInterrupt:
                    sys.exit(130)
        except Exception:
            continue

    print(
        f"[Brahma AI] Error: Python runtime ({sys.executable}) is missing required dependencies (PyQt6).\n"
        "No compatible Python 3.11 or 3.12 interpreter with PyQt6 was found.\n"
        "Please install dependencies or launch via:\n"
        "    py -3.11 main.py\n"
        "or:\n"
        "    .\\EVO.bat",
        file=sys.stderr,
    )
    sys.exit(1)


def configure_warning_filters() -> None:
    """Silence benign third-party library warnings on startup."""
    warnings.filterwarnings(
        "ignore",
        message=".*Revert to STA COM threading mode.*",
        category=UserWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message=".*Direct use of automatic function calling.*",
        category=UserWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message=".*there are non-data parts in the response.*",
        category=UserWarning,
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
    """Apply runtime settings and environment validation. Idempotent."""
    ensure_supported_environment()
    configure_warning_filters()
    configure_qt_environment()
    configure_dpi_awareness()
    configure_qt_attributes()
