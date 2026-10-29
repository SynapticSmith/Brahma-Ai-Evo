from __future__ import annotations

import signal
import threading
from pathlib import Path

import pytest

from agent.loop import AgentLoop
from agent.permissions import PermissionPolicy
from agent.retry import (
    Cancelled,
    backoff_delay,
    call_with_retry,
    http_status,
    is_transient,
)
from agent.runtime import AgentRuntime


class HTTPError(RuntimeError):
    def __init__(self, status: int, message: str = ""):
        self.status = status
        super().__init__(message or f"HTTP {status}")


class Script:
    """Completer that returns or raises the next item, counting calls."""

    def __init__(self, items):
        self.items = list(items)
        self.calls = 0

    def __call__(self, messages, tools):
        self.calls += 1
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _runtime(tmp_path: Path) -> AgentRuntime:
    return AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict({"default": "allow"}),
        approver=None,
        headless=True,
    )


# -- classification --------------------------------------------------------- #


def test_server_and_rate_limit_statuses_are_transient():
    assert is_transient(HTTPError(429))
    assert is_transient(HTTPError(503))
    assert is_transient(HTTPError(408))
    assert not is_transient(HTTPError(400))
    assert not is_transient(HTTPError(401))
    assert not is_transient(HTTPError(404))


def test_socket_failures_are_transient_but_a_refused_port_is_not():
    assert is_transient(TimeoutError("timed out"))
    assert is_transient(ConnectionResetError("reset by peer"))
    assert not is_transient(ConnectionRefusedError("actively refused"))
    assert not is_transient(ValueError("bad json"))


def test_status_is_read_from_the_message_when_there_is_no_attribute():
    assert http_status(RuntimeError("HTTP 429: rate limited")) == 429
    assert is_transient(RuntimeError("error code: 502"))
    assert not is_transient(RuntimeError("HTTP 422: invalid request"))


def test_urllib_reason_is_unwrapped():
    class URLError(OSError):
        def __init__(self, reason):
            self.reason = reason

    assert is_transient(URLError(TimeoutError("read timed out")))
    assert not is_transient(URLError(ConnectionRefusedError("refused")))


def test_backoff_is_exponential_and_capped():
    assert backoff_delay(1, base_delay=1.0, max_delay=10.0) == 1.0
    assert backoff_delay(2, base_delay=1.0, max_delay=10.0) == 2.0
    assert backoff_delay(3, base_delay=1.0, max_delay=10.0) == 4.0
    assert backoff_delay(9, base_delay=1.0, max_delay=5.0) == 5.0
    assert backoff_delay(1, base_delay=0.0) == 0.0


# -- the retry loop --------------------------------------------------------- #


def test_transient_failure_is_retried_then_succeeds():
    calls, sleeps, events = [], [], []

    def op():
        calls.append(1)
        if len(calls) < 3:
            raise HTTPError(503)
        return "ok"

    result = call_with_retry(
        op,
        attempts=3,
        base_delay=1.0,
        jitter=0,
        sleep=sleeps.append,
        on_retry=lambda n, d, e: events.append((n, d)),
    )

    assert result == "ok"
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]
    assert [n for n, _ in events] == [1, 2]


def test_permanent_failure_is_not_retried():
    calls = []

    def op():
        calls.append(1)
        raise HTTPError(400)

    with pytest.raises(HTTPError):
        call_with_retry(op, attempts=3, base_delay=0, sleep=lambda _: None)
    assert len(calls) == 1


def test_gives_up_after_the_attempt_budget():
    calls = []

    def op():
        calls.append(1)
        raise HTTPError(429)

    with pytest.raises(HTTPError):
        call_with_retry(op, attempts=3, base_delay=0, sleep=lambda _: None)
    assert len(calls) == 3


def test_cancel_interrupts_the_backoff_wait():
    cancel = threading.Event()
    calls = []

    def op():
        calls.append(1)
        cancel.set()
        raise HTTPError(503)

    with pytest.raises(Cancelled):
        call_with_retry(op, attempts=5, base_delay=30, cancel=cancel)
    assert len(calls) == 1


def test_a_getter_that_never_fails_calls_once():
    calls = []

    def op():
        calls.append(1)
        return "fine"

    assert call_with_retry(lambda: op(), attempts=3) == "fine"
    assert len(calls) == 1


# -- loop integration ------------------------------------------------------- #


def test_loop_retries_and_reports_the_stall(tmp_path: Path):
    script = Script([HTTPError(503), HTTPError(429), {"role": "assistant", "content": "done"}])
    result = AgentLoop(
        script,
        tools=["read_file"],
        runtime=_runtime(tmp_path),
        retry_attempts=3,
        retry_base_delay=0,
    ).run("go")

    assert result.stop_reason == "completed"
    assert result.text == "done"
    assert script.calls == 3
    assert result.usage["retries"] == 2
    retries = [event for event in result.events if event["type"] == "retry"]
    assert [event["attempt"] for event in retries] == [1, 2]
    assert "503" in retries[0]["error"]


def test_loop_does_not_retry_a_permanent_error(tmp_path: Path):
    script = Script([HTTPError(400, "bad request")])
    result = AgentLoop(
        script,
        tools=["read_file"],
        runtime=_runtime(tmp_path),
        retry_attempts=3,
        retry_base_delay=0,
    ).run("go")

    assert result.stop_reason == "error"
    assert script.calls == 1
    assert "bad request" in result.text
    assert not [event for event in result.events if event["type"] == "retry"]


def test_loop_stops_before_calling_when_cancelled(tmp_path: Path):
    cancel = threading.Event()
    cancel.set()
    script = Script([{"role": "assistant", "content": "should not run"}])
    result = AgentLoop(
        script, tools=["read_file"], runtime=_runtime(tmp_path), cancel=cancel
    ).run("go")

    assert result.stop_reason == "cancelled"
    assert script.calls == 0


def test_loop_returns_cancelled_when_the_flag_flips_during_backoff(tmp_path: Path):
    cancel = threading.Event()

    def completer(messages, tools):
        cancel.set()
        raise HTTPError(503)

    result = AgentLoop(
        completer,
        tools=["read_file"],
        runtime=_runtime(tmp_path),
        cancel=cancel,
        retry_attempts=3,
        retry_base_delay=0,
    ).run("go")

    assert result.stop_reason == "cancelled"


# -- runtime flag ----------------------------------------------------------- #


def test_runtime_cancel_is_sticky_until_reset(tmp_path: Path):
    runtime = _runtime(tmp_path)
    assert not runtime.cancel_event.is_set()
    runtime.cancel()
    assert runtime.cancel_event.is_set()
    runtime.reset_cancel()
    assert not runtime.cancel_event.is_set()


def test_cli_handler_cancels_then_hard_exits(tmp_path: Path, capsys):
    from agent import __main__ as cli

    runtime = _runtime(tmp_path)
    restore = cli._install_cancel(runtime)
    try:
        handler = signal.getsignal(signal.SIGINT)
        handler(signal.SIGINT, None)
        assert runtime.cancel_event.is_set()
        assert "cancelling" in capsys.readouterr().err
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGINT, None)
    finally:
        restore()
