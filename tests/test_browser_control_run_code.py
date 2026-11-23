import pytest

from actions import browser_control as browser_control_module
from actions import playwright_mcp_client
from actions.playwright_mcp_client import PlaywrightMCPClient


class _FakeMCPClient:
    def run_code_unsafe(self, code):
        raise AssertionError("run_code_unsafe must not be called")


@pytest.fixture
def fake_mcp(monkeypatch):
    client = _FakeMCPClient()
    monkeypatch.setattr(
        playwright_mcp_client,
        "get_playwright_mcp_client",
        lambda: client,
    )
    return client


@pytest.mark.parametrize("action", ("run_code", "execute"))
def test_unsafe_code_actions_are_disabled(fake_mcp, action):
    result = browser_control_module.browser_control(
        {"action": action, "code": "print('unsafe')"}
    )

    assert fake_mcp is not None
    assert result == "Unsafe code execution is disabled."
    assert not hasattr(PlaywrightMCPClient, "run_code_unsafe")
