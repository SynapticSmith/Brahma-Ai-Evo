import importlib
import threading

import workspace_store


def _fresh_module(tmp_path, monkeypatch):
    module = importlib.reload(workspace_store)
    monkeypatch.setattr(
        module, "STORE_FILE", tmp_path / "config" / "workspace_store.sqlite3"
    )
    return module


def test_store_is_lazy_until_first_use(tmp_path, monkeypatch):
    module = _fresh_module(tmp_path, monkeypatch)

    assert module._STORE is None
    assert not (tmp_path / "config").exists()

    store = module.store()

    assert module._STORE is store
    assert module.get_workspace_store() is store


def test_store_construction_is_thread_safe(tmp_path, monkeypatch):
    module = _fresh_module(tmp_path, monkeypatch)
    stores = []
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        stores.append(module.store())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(stores) == 4
    assert all(store is stores[0] for store in stores)
