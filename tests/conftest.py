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

import pytest


@pytest.fixture(autouse=True)
def _reset_active_peer_service():
    yield
    try:
        from core.peer.service import set_active_peer_service
        set_active_peer_service(None)
    except Exception:
        pass

