import concurrent.futures
from pathlib import Path
import sqlite3
from unittest.mock import MagicMock

from smart_home.storage import SmartHomeStorage
from core.ui_devices import SmartDevicesSection


def test_smart_home_storage_initializes_and_closes_connections(tmp_path: Path):
    db_file = tmp_path / "test_smart_home.sqlite3"
    storage = SmartHomeStorage(db_path=db_file)

    # Initialized in WAL mode
    with sqlite3.connect(db_file) as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode.lower() == "wal"

    # Save account and device
    acc_id = storage.save_provider_account("mock_p", "My Hub", {"token": "secret123"})
    assert acc_id
    acc = storage.get_provider_account(acc_id)
    assert acc is not None
    assert acc["account_label"] == "My Hub"
    assert acc["credentials"] == {"token": "secret123"}

    dev_ids = storage.save_devices(
        acc_id,
        "mock_p",
        [
            {
                "external_id": "ext-1",
                "name": "Living Room Light",
                "room": "Living Room",
                "device_type": "light",
                "is_on": True,
            }
        ],
    )
    assert len(dev_ids) == 1

    devices = storage.list_devices()
    assert len(devices) == 1
    assert devices[0]["name"] == "Living Room Light"
    assert devices[0]["is_on"] is True


def test_smart_home_storage_concurrent_access_no_io_error(tmp_path: Path):
    db_file = tmp_path / "test_concurrent.sqlite3"
    storage = SmartHomeStorage(db_path=db_file)
    acc_id = storage.save_provider_account("p1", "Hub", {"key": "123"})

    errors = []

    def writer(i: int):
        try:
            storage.save_devices(
                acc_id,
                "p1",
                [{"external_id": f"ext-{i}", "name": f"Device {i}", "room": "Office", "is_on": bool(i % 2)}],
            )
        except Exception as exc:
            errors.append(exc)

    def reader():
        try:
            for _ in range(20):
                storage.list_devices()
        except Exception as exc:
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = []
        for i in range(15):
            futures.append(ex.submit(writer, i))
            futures.append(ex.submit(reader))
        concurrent.futures.wait(futures)

    assert errors == []
    devices = storage.list_devices()
    assert len(devices) == 15


def test_smart_devices_section_refresh_handles_storage_error(monkeypatch):
    section = SmartDevicesSection.__new__(SmartDevicesSection)
    section._service = MagicMock()
    section._service.list_devices.side_effect = sqlite3.OperationalError("disk I/O error")

    # Should not raise exception
    section.refresh(force=False)
    section._service.list_devices.assert_called_once()
