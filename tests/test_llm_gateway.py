"""Unit tests for core.llm_gateway -- the single auxiliary-LLM egress (log section 14).

Every provider link is monkeypatched to a local recorder: these tests make no
network calls and never import the real google.genai. What they pin down is the
routing contract from the gateway's docstring: chain order (selected provider
first), offline cloud-drop, "a disabled provider is never contacted", the
AuxResponse SDK shape, the ``links=`` restriction for grounded tasks, and
GatewayError joining every link's reason.
"""

import base64
from types import SimpleNamespace

import pytest

from core import llm_gateway


class _LinkRecorder:
    """Stands in for every provider link; records which pids the gateway dialed."""

    def __init__(self, answers=None, fail=()):
        self.calls = []  # every pid the gateway asked, in order
        self.answers = answers or {}
        self.fail = set(fail)

    def chat(self, pid, text, images=None, system=None, temperature=None,
             max_tokens=None):
        self.calls.append(pid)
        if pid in self.fail:
            raise RuntimeError(f"{pid} transport is down")
        return self.answers.get(pid, f"answer from {pid}")

    def gemini(self, contents, model, config, api_key):
        self.calls.append("gemini")
        if "gemini" in self.fail:
            raise RuntimeError("gemini quota exhausted")
        return self.answers.get("gemini", f"answer from gemini ({model})")


@pytest.fixture
def links(monkeypatch):
    """Install a recorder in place of both link implementations."""
    rec = _LinkRecorder()
    monkeypatch.setattr(llm_gateway, "_chat_link", rec.chat)
    monkeypatch.setattr(llm_gateway, "_call_gemini", rec.gemini)
    return rec


def _pin_chain(monkeypatch, chain, offline=False, selected=None):
    """Pin the gateway's chain computation, including this machine's settings."""
    monkeypatch.setattr(llm_gateway, "_chain", lambda: list(chain))
    monkeypatch.setattr(llm_gateway, "_offline", lambda: offline)
    monkeypatch.setattr(llm_gateway, "_selected_id", lambda: selected)


# --------------------------------------------------------------- AuxResponse


def test_auxresponse_has_the_sdk_shape():
    r = llm_gateway.AuxResponse("hello", "local")
    assert r.text == "hello"
    assert r.provider == "local"
    part = r.candidates[0].content.parts[0]
    assert part.text == "hello"


# ------------------------------------------------------------ chain ordering


def test_chain_places_the_selected_provider_first(monkeypatch):
    from core import provider_router

    monkeypatch.setattr(provider_router, "chain",
                        lambda: ["gemini", "openrouter", "local", "custom"])
    monkeypatch.setattr(llm_gateway, "_offline", lambda: False)
    monkeypatch.setattr(llm_gateway, "_selected_id", lambda: "local")
    assert llm_gateway._chain() == ["local", "gemini", "openrouter", "custom"]


def test_offline_mode_drops_the_cloud_links(monkeypatch):
    from core import provider_router

    monkeypatch.setattr(provider_router, "chain",
                        lambda: ["gemini", "openrouter", "local", "custom"])
    monkeypatch.setattr(llm_gateway, "_offline", lambda: True)
    monkeypatch.setattr(llm_gateway, "_selected_id", lambda: None)
    assert llm_gateway._chain() == ["local", "custom"]


def test_offline_mode_keeps_the_local_selection_first(monkeypatch):
    from core import provider_router

    monkeypatch.setattr(provider_router, "chain",
                        lambda: ["gemini", "openrouter", "local", "custom"])
    monkeypatch.setattr(llm_gateway, "_offline", lambda: True)
    monkeypatch.setattr(llm_gateway, "_selected_id", lambda: "custom")
    assert llm_gateway._chain() == ["custom", "local"]


def test_a_disabled_provider_cannot_be_moved_first_by_the_selection(monkeypatch):
    # The selection only reorders providers that are *in* the chain; a
    # disabled provider stays out even when Settings still names it.
    from core import provider_router

    monkeypatch.setattr(provider_router, "chain",
                        lambda: ["gemini", "local"])  # openrouter disabled
    monkeypatch.setattr(llm_gateway, "_offline", lambda: False)
    monkeypatch.setattr(llm_gateway, "_selected_id", lambda: "openrouter")
    assert llm_gateway._chain() == ["gemini", "local"]


# ------------------------------------------------------------- routing rules


def test_first_link_answers_and_only_it_is_dialed(monkeypatch, links, capsys):
    _pin_chain(monkeypatch, ["local"])
    out = llm_gateway.generate_content("hi", purpose="unit test")
    assert out.text == "answer from local"
    assert out.provider == "local"
    assert links.calls == ["local"]
    assert "answered by" not in capsys.readouterr().out


def test_disabled_providers_are_never_contacted(monkeypatch, links):
    # Only local is in the chain: gemini is not even attempted as a fallback.
    _pin_chain(monkeypatch, ["local"])
    llm_gateway.generate_content("hi", purpose="unit test")
    assert "gemini" not in links.calls
    assert links.calls == ["local"]


def test_failover_walks_the_chain_and_names_the_link_that_answered(
        monkeypatch, links, capsys):
    links.fail = {"gemini", "openrouter"}
    _pin_chain(monkeypatch, ["gemini", "openrouter", "local"])
    out = llm_gateway.generate_content("hi", purpose="unit test")
    assert out.provider == "local"
    assert links.calls == ["gemini", "openrouter", "local"]
    log = capsys.readouterr().out
    assert "[LLM Gateway] unit test: gemini failed" in log
    assert "[LLM Gateway] unit test: openrouter failed" in log
    assert "[LLM Gateway] unit test: answered by local" in log


def test_every_link_failure_joins_the_gateway_error(monkeypatch, links):
    links.fail = {"gemini", "local"}
    _pin_chain(monkeypatch, ["gemini", "local"])
    with pytest.raises(llm_gateway.GatewayError) as exc:
        llm_gateway.generate_content("hi", purpose="unit test")
    msg = str(exc.value)
    assert "gemini: gemini quota exhausted" in msg
    assert "local: local transport is down" in msg


def test_an_empty_response_counts_as_a_failure(monkeypatch, links):
    links.answers = {"local": "", "custom": " fallback "}
    _pin_chain(monkeypatch, ["local", "custom"])
    out = llm_gateway.generate_content("hi", purpose="unit test")
    assert out.provider == "custom"
    assert out.text == "fallback"


def test_all_empty_responses_raise_with_the_reason(monkeypatch, links):
    links.answers = {"local": ""}
    _pin_chain(monkeypatch, ["local"])
    with pytest.raises(llm_gateway.GatewayError) as exc:
        llm_gateway.generate_content("hi", purpose="unit test")
    assert "empty response" in str(exc.value)


def test_an_empty_chain_raises_instead_of_answering(monkeypatch, links):
    _pin_chain(monkeypatch, [])
    with pytest.raises(llm_gateway.GatewayError) as exc:
        llm_gateway.generate_content("hi", purpose="unit test")
    assert "no providers are enabled" in str(exc.value)


# --------------------------------------------------------- grounded allowlist


def test_grounding_link_restriction_refuses_and_dials_nothing(
        monkeypatch, links):
    # web search runs with links=("gemini",): with Gemini off the call must
    # fail loudly (callers fall back to DDG) rather than let an ungrounded
    # model answer from memory.
    _pin_chain(monkeypatch, ["local", "custom"])
    with pytest.raises(llm_gateway.GatewayError) as exc:
        llm_gateway.generate_content("q", links=("gemini",), purpose="search")
    assert "disabled in Settings" in str(exc.value)
    assert links.calls == []


def test_grounding_link_restriction_still_reaches_gemini(monkeypatch, links):
    _pin_chain(monkeypatch, ["gemini", "local"])
    out = llm_gateway.generate_content("q", links=("gemini",), purpose="search")
    assert out.provider == "gemini"
    assert links.calls == ["gemini"]


# ------------------------------------------------------------ request parsing


def test_split_contents_flattens_text_and_image_parts():
    raw = b"\x89PNG fake"
    jpeg = b"\xff\xd8fake jpeg"
    contents = [
        "look at this",
        {"inline_data": {"data": base64.b64encode(raw).decode("ascii"),
                         "mime_type": "image/png"}},
        SimpleNamespace(text="and this", inline_data=None),
        # Request-side (attribute-style) part with raw bytes.
        SimpleNamespace(text=None,
                        inline_data=SimpleNamespace(data=jpeg,
                                                    mime_type="image/jpeg")),
    ]
    text, images = llm_gateway._split_contents(contents)
    assert text == "look at this\nand this"
    assert images == [(raw, "image/png"), (jpeg, "image/jpeg")]


def test_system_text_flattens_a_types_shaped_instruction():
    system = SimpleNamespace(parts=[SimpleNamespace(text="You are terse."),
                                    SimpleNamespace(text="Be helpful.")])
    config = {"system_instruction": system}
    assert llm_gateway._system_text(config) == "You are terse.\nBe helpful."


def test_merged_config_is_untouched_without_overrides():
    config = {"temperature": 0.2, "google_search": True}
    assert llm_gateway._merged_config(config, None, None, None) is config


def test_merged_config_layers_explicit_overrides():
    merged = llm_gateway._merged_config(
        {"temperature": 0.2}, "sys", None, 256)
    assert merged == {"temperature": 0.2, "system_instruction": "sys",
                      "max_output_tokens": 256}


def test_model_and_config_reach_the_gemini_link(monkeypatch, links):
    seen = {}
    _orig = links.gemini

    def _capture(contents, model, config, api_key):
        seen.update(model=model, config=config)
        return _orig(contents, model, config, api_key)

    monkeypatch.setattr(llm_gateway, "_call_gemini", _capture)
    _pin_chain(monkeypatch, ["gemini"])
    llm_gateway.generate_content("hi", temperature=0.5, purpose="unit test")
    assert seen["model"] == llm_gateway.DEFAULT_GEMINI_MODEL
    assert seen["config"] == {"temperature": 0.5}
