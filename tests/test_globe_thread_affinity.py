import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from core import globe_window
from core.globe_window import GlobeWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _WorkerThread:
    pass


class _ThreadingStub:
    @staticmethod
    def current_thread():
        return _WorkerThread()

    @staticmethod
    def main_thread():
        return "main"


class _NoTimer:
    @staticmethod
    def singleShot(*args, **kwargs):
        raise AssertionError("QTimer.singleShot must not be used from a worker thread")


class _FakePage:
    def __init__(self):
        self.calls = []

    def runJavaScript(self, script, callback=None):
        self.calls.append((script, callback))


class _FakeWebView:
    def __init__(self):
        self.page_obj = _FakePage()

    def page(self):
        return self.page_obj


def _bare_window(qapp):
    assert qapp is not None
    window = GlobeWindow.__new__(GlobeWindow)
    QWidget.__init__(window)
    window._web_view = None
    window._cmd_sig.connect(window._handle_cmd)
    return window


def test_inspect_current_view_from_worker_uses_cmd_signal(qapp, monkeypatch):
    window = _bare_window(qapp)
    window._web_view = _FakeWebView()
    monkeypatch.setattr(globe_window, "threading", _ThreadingStub)
    monkeypatch.setattr(globe_window, "QTimer", _NoTimer)

    emitted = []
    window._cmd_sig.disconnect(window._handle_cmd)
    window._cmd_sig.connect(lambda payload: emitted.append(payload))

    def callback(result):
        pass

    window.inspect_current_view(callback)

    assert len(emitted) == 1
    assert emitted[0]["action"] == "inspect"
    assert emitted[0]["callback"] is callback
    assert window._web_view.page_obj.calls == []


def test_handle_inspect_runs_query_and_preserves_callback(qapp, monkeypatch):
    window = _bare_window(qapp)
    web_view = _FakeWebView()
    window._web_view = web_view
    monkeypatch.setattr(globe_window, "reverse_geocode_area", lambda lat, lon: "Area")
    monkeypatch.setattr(
        globe_window,
        "fetch_live_flights_in_bounds",
        lambda *args: [{"id": "one"}, {"id": "two"}],
    )

    results = []

    def callback(result):
        results.append(result)

    window._handle_cmd({"action": "inspect", "callback": callback})

    assert len(web_view.page_obj.calls) == 1
    script, js_callback = web_view.page_obj.calls[0]
    assert "getCurrentView" in script

    js_callback(
        {
            "centerLat": "10.126",
            "centerLon": "20.254",
            "bounds": {
                "minLat": 5,
                "maxLat": 15,
                "minLon": 15,
                "maxLon": 25,
            },
        }
    )

    assert results == [
        {
            "center_lat": 10.13,
            "center_lon": 20.25,
            "area_name": "Area",
            "flight_count": 2,
            "flights_sample": [{"id": "one"}, {"id": "two"}],
        }
    ]
