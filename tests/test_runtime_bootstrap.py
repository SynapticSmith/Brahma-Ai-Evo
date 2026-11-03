from unittest.mock import patch
import warnings
from core.qt_bootstrap import (
    bootstrap,
    configure_warning_filters,
    ensure_supported_environment,
)
from core.sensorium import PassiveSensorium


def test_warning_filters_silence_pywinauto_sta():
    configure_warning_filters()
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        configure_warning_filters()
        warnings.warn("Revert to STA COM threading mode", UserWarning)

    # Should be filtered out
    matching = [w for w in recorded if "Revert to STA COM threading mode" in str(w.message)]
    assert matching == []


def test_bootstrap_idempotence():
    # Calling bootstrap multiple times in process must not throw
    bootstrap()
    bootstrap()


def test_ensure_supported_environment_short_circuits():
    # When BRAHMA_NO_RELAUNCH is set, returns immediately without relaunching
    with patch.dict("os.environ", {"BRAHMA_NO_RELAUNCH": "1"}):
        ensure_supported_environment()


def test_sensorium_fallback_without_psutil():
    # Ensure sensorium functions cleanly when psutil is None
    sensorium = PassiveSensorium(poll_interval=100.0)
    with patch("core.sensorium.psutil", None):
        # Update telemetry should not raise
        sensorium._update_telemetry()
        assert sensorium.cpu_percent == 0.0
        assert sensorium.ram_percent == 0.0
        assert sensorium.battery_percent is None
        assert sensorium.power_plugged is None

        # Snapshot should return valid safe structure
        snap = sensorium.get_snapshot()
        assert snap["cpu_percent"] == 0.0
        assert snap["ram_percent"] == 0.0
        assert snap["process_name"] == "Unknown"
