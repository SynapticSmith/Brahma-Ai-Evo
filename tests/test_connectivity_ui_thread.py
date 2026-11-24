import os
import threading

import pytest


@pytest.fixture(scope="module")
def _app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def page(_app):
    import ui

    return ui.SystemConnectivityPage()


def test_run_on_ui_calls_inline_on_gui_thread(page):
    called = []

    page._run_on_ui(lambda: called.append(True))

    assert called == [True]


def test_run_on_ui_queues_from_worker_thread(page, monkeypatch):
    from core import ui_connectivity

    scheduled = []
    monkeypatch.setattr(
        ui_connectivity.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )
    called = []

    def work():
        page._run_on_ui(lambda: called.append(True))

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()

    assert len(scheduled) == 1
    assert scheduled[0][0] == 0
    assert called == []
    scheduled[0][1]()
    assert called == [True]
