"""Regression tests for auxiliary-provider routing consistency (WP08-08)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import llm_gateway, reply_flow
from core.shopping_agent import ShoppingAgent, ShoppingItem

HARDCODED_MODEL = "gemini-2.5-flash"
_RESPONSE = SimpleNamespace(text=json.dumps({
    "intent": "IGNORE",
    "reply_text": "",
    "verdict": "ok",
    "voice_summary": "ok",
    "best_overall_index": 0,
    "best_budget_index": 0,
    "item_insights": [],
}))


@pytest.fixture
def gateway_calls(monkeypatch):
    calls = []

    def fake_generate_content(contents=None, **kwargs):
        calls.append({"contents": contents, "kwargs": kwargs})
        return _RESPONSE

    monkeypatch.setattr(llm_gateway, "generate_content", fake_generate_content)
    return calls


def test_ig_reply_uses_active_provider_router(gateway_calls):
    reply_flow.ig_gemini_reply("friend", "hello")

    assert len(gateway_calls) == 1
    assert "model" not in gateway_calls[0]["kwargs"]
    assert gateway_calls[0]["kwargs"]["purpose"] == "Instagram reply"


def test_ig_intent_parser_uses_active_provider_router(gateway_calls):
    flow = object.__new__(reply_flow.ReplyFlow)

    assert flow.parse_ig_reply_intent("hello") == ("IGNORE", "")
    assert len(gateway_calls) == 1
    assert "model" not in gateway_calls[0]["kwargs"]
    assert gateway_calls[0]["kwargs"]["purpose"] == "Instagram reply intent"


def test_shopping_synthesis_uses_active_provider_router(gateway_calls):
    agent = object.__new__(ShoppingAgent)
    item = ShoppingItem(title="Test charger", price=1499.0, platform="amazon")

    result = agent.synthesize_rufus_recommendation("charger", [item], [])

    assert result["best_overall"] is item
    assert len(gateway_calls) == 1
    assert "model" not in gateway_calls[0]["kwargs"]
    assert gateway_calls[0]["kwargs"]["purpose"] == "shopping recommendation synthesis"


@pytest.mark.parametrize("relative_path", ("core/reply_flow.py", "core/shopping_agent.py"))
def test_call_sites_do_not_hardcode_gemini_model(relative_path):
    path = Path(__file__).resolve().parents[1] / relative_path

    assert HARDCODED_MODEL not in path.read_text(encoding="utf-8")
