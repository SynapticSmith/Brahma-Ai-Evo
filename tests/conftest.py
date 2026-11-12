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


@pytest.fixture(autouse=True, scope="session")
def _isolate_peer_storage(tmp_path_factory):
    """Keep every test out of the operator's real peer store.

    ``PeerTaskStore()``, ``PeerArtifactStore()`` and ``PeerRegistry()`` with no
    argument all resolve ``%LOCALAPPDATA%\\BrahmaAI\\peer``. A helper that lets
    one of them default would create - and on an artifact upload, write - the
    operator's real trust store and task database. Redirect the three default
    paths once for the whole session; an explicit path is untouched.
    """
    import pytest as _pytest

    peer_root = tmp_path_factory.mktemp("peer_userdata")
    patch = _pytest.MonkeyPatch()
    try:
        import core.peer.artifacts as artifacts
        import core.peer.pairing as pairing
        import core.peer.store as peer_store

        patch.setattr(artifacts, "get_user_data_dir", lambda: peer_root)
        patch.setattr(pairing, "get_user_data_dir", lambda: peer_root)
        patch.setattr(peer_store, "default_peer_db_path", lambda: peer_root / "tasks.db")
        yield
    finally:
        patch.undo()

