import sys
from pathlib import Path

# Ensure tests can import the application package from the workspace root.
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Qt runtime setup (Chromium flags + shared GL contexts) is no longer a module
# import side effect, so tests that assert it (test_globe_performance,
# test_background_orb_startup) apply it here explicitly and deterministically.
from core.qt_bootstrap import bootstrap

bootstrap()
