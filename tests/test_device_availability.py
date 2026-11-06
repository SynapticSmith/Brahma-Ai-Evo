"""Availability must be truthful, not a dead end (ROUTING-AUDIT §6 Phase 2).

`smart_home_control` returns the service's no-devices message instead of
raising. The connect_* tools need the same property: "nothing is paired" and
"that name does not match a paired device" are different answers, and only the
first one tells the model to ask a pairing question. Without the distinction a
zero-device machine reads as a typo in the device name.
"""

from __future__ import annotations

import json

import actions.brahma_connect as connect


class _EmptyService:
    def list_devices(self):
        return []

    def get_device(self, _target):  # pragma: no cover - must not be reached
        raise AssertionError("lookup must not run when nothing is paired")

    def route_command(self, *_args, **_kwargs):  # pragma: no cover - same
        raise AssertionError("execution must not run when nothing is paired")


class _PairedService:
    def __init__(self, devices=None, route_result=None):
        self._devices = devices or [{"device_id": "d1", "name": "Pixel"}]
        self._route_result = route_result or {
            "success": False,
            "error": "No device matches 'old phone'.",
            "error_code": "DEVICE_NOT_FOUND",
        }
        self.routed = []

    def list_devices(self):
        return list(self._devices)

    def route_command(self, target, action, parameters):
        self.routed.append((target, action, parameters))
        return dict(self._route_result)


class _BrokenGateway:
    def list_devices(self):
        raise RuntimeError("gateway is down")

    def get_device(self, _target):
        return None

    def resolve_devices(self, _target):
        raise RuntimeError("gateway is down")


def _use(monkeypatch, service):
    monkeypatch.setattr(connect, "_service", lambda: service)


def _payload(raw: str) -> dict:
    return json.loads(raw)


def test_list_devices_reports_no_pairing_instead_of_a_bare_zero(monkeypatch):
    _use(monkeypatch, _EmptyService())

    payload = _payload(connect.connect_list_devices({}))

    assert payload["success"] is True
    assert payload["count"] == 0
    assert payload["paired"] is False
    assert "No phone or tablet is paired" in payload["note"]


def test_list_devices_with_devices_is_still_paired(monkeypatch):
    _use(monkeypatch, _PairedService())

    payload = _payload(connect.connect_list_devices({}))

    assert payload["paired"] is True
    assert "note" not in payload


def test_execute_on_an_unpaired_machine_says_so(monkeypatch):
    _use(monkeypatch, _EmptyService())

    payload = _payload(
        connect.connect_execute({"device": "phone", "action": "battery"})
    )

    assert payload["error_code"] == "NO_PAIRED_DEVICE"
    assert "No phone or tablet is paired" in payload["error"]


def test_execute_with_a_paired_device_keeps_the_real_not_found_error(monkeypatch):
    service = _PairedService()
    _use(monkeypatch, service)

    payload = _payload(
        connect.connect_execute({"device": "old phone", "action": "battery"})
    )

    assert payload["error_code"] == "DEVICE_NOT_FOUND"
    assert "No device matches 'old phone'" in payload["error"]
    assert service.routed, "a paired gateway must still receive the command"


def test_get_device_on_an_unpaired_machine_does_not_read_as_a_typo(monkeypatch):
    _use(monkeypatch, _EmptyService())

    payload = _payload(connect.connect_get_device({"device": "pixel"}))

    assert payload["error_code"] == "NO_PAIRED_DEVICE"
    assert "No device matches" not in payload["error"]


def test_a_broken_gateway_is_not_mistaken_for_an_empty_pairing_list(monkeypatch):
    _use(monkeypatch, _BrokenGateway())

    payload = _payload(connect.connect_get_device({"device": "pixel"}))

    assert payload["error_code"] == "GATEWAY_UNAVAILABLE"
    assert payload["error_code"] != "NO_PAIRED_DEVICE"
