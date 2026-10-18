"""Unit tests for core.local_brain -- the offline endpoint contract (log sections 16-17).

What these pin down:

* the context-budget contract from the system-awareness prompt rewrite:
  ``chat_complete`` and the streaming path must request an explicit
  ``num_ctx``. Ollama's OpenAI-compatible endpoint silently truncates to its
  own default (2048-4096 tokens), which is smaller than the system prompt the
  app actually sends (core/prompt.txt is ~15KB on its own), so without the
  budget the offline 3B model would lose its operating instructions before
  the user's turn.
* the hardware-aware timeout-profile contract: a CPU-resident model gets the
  slow budgets (prefill of the ~4K-token context can take minutes), a
  VRAM-resident one keeps the fast budgets, an inconclusive probe never
  invents patience, an explicit Settings value beats the auto-detection, and
  both budgets stay finite so a wedged server still becomes a surfaced
  error instead of a hang. The gateway's local link borrows the same
  budget (llm_client._local_read_timeout) instead of a second constant.

No network: ``_post_json`` / ``_open_stream`` / ``_get_json`` are replaced
with recorders.
"""

import types

import llm_client as llm_client_mod
from core import local_brain as local_brain_mod
from core.local_brain import (
    LOCAL_NUM_CTX,
    STREAM_IDLE_TIMEOUT,
    TIMEOUT_PROFILES,
    LocalBrain,
    LocalBrainError,
    _cpu_resident_from_ps,
)


def _pinned_brain() -> LocalBrain:
    """A LocalBrain whose endpoint/model are constructor-pinned, so the real
    Settings file on this machine cannot leak into a payload assertion."""
    return LocalBrain(endpoint="http://test-host:11434/v1", default_model="test-model")


# --------------------------------------------------------------- chat_complete


def test_chat_complete_requests_an_explicit_context_budget(monkeypatch):
    """The non-streaming payload carries options.num_ctx.

    This is the regression guard for silent truncation: if someone removes the
    budget to "clean up" the payload, the offline model starts ignoring the
    prompt tail and this test fails.
    """
    brain = _pinned_brain()
    captured = {}

    def fake_post(payload, timeout):
        captured["payload"] = payload
        captured["timeout"] = timeout
        return '{"choices": [{"message": {"content": "ok"}}]}'

    monkeypatch.setattr(brain, "_post_json", fake_post)
    brain.chat_complete(
        messages=[{"role": "user", "content": "hi"}],
    )

    assert captured["payload"]["options"] == {"num_ctx": LOCAL_NUM_CTX}


def test_chat_complete_context_budget_fits_the_system_prompt():
    """The budget must be larger than the prompt it protects.

    core/prompt.txt is the bulk of what the offline path sends; a budget below
    its own size would truncate even before skill context and tools.
    """
    from pathlib import Path

    prompt = (Path(__file__).resolve().parent.parent / "core" / "prompt.txt")
    prompt_bytes = prompt.stat().st_size if prompt.exists() else 0

    # 8192 tokens comfortably exceeds the ~15KB prompt (roughly 4 chars/token
    # for English prose => ~3.7K tokens) while staying inside the bundled 3B
    # models' native 32K window.
    assert LOCAL_NUM_CTX >= 8192
    # Guard the direction of the guarantee: the budget is a token count, the
    # prompt a byte count; 4 bytes/token is a conservative English estimate.
    assert LOCAL_NUM_CTX * 4 > prompt_bytes, (
        "LOCAL_NUM_CTX no longer covers core/prompt.txt; raise the budget or shrink the prompt"
    )


def test_chat_complete_model_and_endpoint_are_used(monkeypatch):
    """Sanity: the recorder saw the pinned model and the pinned host, so the
    assertions above were not vacuous."""
    brain = _pinned_brain()
    captured = {}

    def fake_post(payload, timeout):
        captured["payload"] = payload
        return '{"choices": [{"message": {"content": "ok"}}]}'

    monkeypatch.setattr(brain, "_post_json", fake_post)
    brain.chat_complete(messages=[{"role": "user", "content": "hi"}])

    assert captured["payload"]["model"] == "test-model"
    assert brain.endpoint == "http://test-host:11434/v1"


def test_chat_complete_surfaces_error_field_as_local_brain_error(monkeypatch):
    """An `error` body becomes LocalBrainError, not a silent empty answer."""
    brain = _pinned_brain()
    monkeypatch.setattr(
        brain, "_post_json", lambda payload, timeout: '{"error": "model not found"}'
    )

    try:
        brain.chat_complete(messages=[{"role": "user", "content": "hi"}])
    except LocalBrainError as exc:
        assert "model not found" in str(exc)
    else:
        raise AssertionError("expected LocalBrainError for an error body")


def test_chat_complete_rejects_missing_choices(monkeypatch):
    """A body without choices is a failure, never an empty success."""
    brain = _pinned_brain()
    monkeypatch.setattr(brain, "_post_json", lambda payload, timeout: "{}")

    try:
        brain.chat_complete(messages=[{"role": "user", "content": "hi"}])
    except LocalBrainError as exc:
        assert "no choices" in str(exc)
    else:
        raise AssertionError("expected LocalBrainError for a body without choices")


def test_tools_are_formatted_but_do_not_drop_the_budget(monkeypatch):
    """A tool-bearing turn still carries num_ctx: the tool surface is what
    pushes the prompt over the default window in the first place."""
    brain = _pinned_brain()
    captured = {}

    def fake_post(payload, timeout):
        captured["payload"] = payload
        return '{"choices": [{"message": {"content": "ok"}}]}'

    monkeypatch.setattr(brain, "_post_json", fake_post)
    tools = [
        {
            "name": "open_app",
            "description": "Opens an application.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"app_name": {"type": "STRING"}},
            },
        }
    ]
    brain.chat_complete(messages=[{"role": "user", "content": "open chrome"}], tools=tools)

    assert captured["payload"]["options"] == {"num_ctx": LOCAL_NUM_CTX}
    assert captured["payload"]["tools"], "tools should be present in the payload"


def test_tools_use_lowercase_json_schema(monkeypatch):
    """Gemini-style OBJECT/STRING schema types are lowercased for the local
    runtime, which only understands JSON-Schema casing."""
    brain = _pinned_brain()
    captured = {}

    def fake_post(payload, timeout):
        captured["payload"] = payload
        return '{"choices": [{"message": {"content": "ok"}}]}'

    monkeypatch.setattr(brain, "_post_json", fake_post)
    tools = [
        {
            "name": "reminder",
            "description": "Sets a reminder.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"message": {"type": "STRING"}},
                "required": ["message"],
            },
        }
    ]
    brain.chat_complete(messages=[{"role": "user", "content": "remind me"}], tools=tools)

    sent = captured["payload"]["tools"][0]["function"]
    assert sent["parameters"]["type"] == "object"
    assert sent["parameters"]["properties"]["message"]["type"] == "string"


# ------------------------------------------------------- timeout profiles


def _brain_with_settings(monkeypatch, settings) -> LocalBrain:
    """A pinned brain whose Settings are the given dict (not this machine's)."""
    monkeypatch.setattr(local_brain_mod, "_load_settings", lambda: dict(settings))
    return _pinned_brain()


def _fake_ps(size_vram, name="test-model:latest"):
    return {"models": [{"name": name, "size_vram": size_vram}]}


def _capture_timeout(monkeypatch, brain):
    """Replace _post_json with a recorder; returns the captured-dict."""
    captured = {}

    def fake_post(payload, timeout):
        captured["timeout"] = timeout
        return '{"choices": [{"message": {"content": "ok"}}]}'

    monkeypatch.setattr(brain, "_post_json", fake_post)
    return captured


def test_cpu_resident_model_gets_slow_budgets(monkeypatch):
    """A model Ollama reports with zero VRAM is on the CPU: prefill of the
    system context can take minutes, so chat_complete must ask for the slow
    whole-body budget, not the GPU-era 180 s."""
    brain = _brain_with_settings(monkeypatch, {})
    monkeypatch.setattr(brain, "_get_json", lambda url: _fake_ps(0))
    captured = _capture_timeout(monkeypatch, brain)

    brain.chat_complete(messages=[{"role": "user", "content": "hi"}])

    assert brain.active_profile() == "slow"
    assert captured["timeout"] == TIMEOUT_PROFILES["slow"]["response"] == 600.0


def test_vram_resident_model_keeps_fast_budgets(monkeypatch):
    """Any VRAM residency (even partial offload) keeps the historical fast
    budget, which is also the better failure bound."""
    brain = _brain_with_settings(monkeypatch, {})
    monkeypatch.setattr(brain, "_get_json", lambda url: _fake_ps(8_000_000_000))
    captured = _capture_timeout(monkeypatch, brain)

    brain.chat_complete(messages=[{"role": "user", "content": "hi"}])

    assert brain.active_profile() == "fast"
    assert captured["timeout"] == TIMEOUT_PROFILES["fast"]["response"] == 180.0


def test_probe_failure_keeps_fast_budgets(monkeypatch):
    """A failed probe (non-Ollama server, wedged runtime) never invents
    patience: fast stays the default."""
    brain = _brain_with_settings(monkeypatch, {})

    def boom(url):
        raise LocalBrainError("probe failed")

    monkeypatch.setattr(brain, "_get_json", boom)
    captured = _capture_timeout(monkeypatch, brain)

    brain.chat_complete(messages=[{"role": "user", "content": "hi"}])

    assert brain.active_profile() == "fast"
    assert captured["timeout"] == TIMEOUT_PROFILES["fast"]["response"]


def test_settings_profile_override_beats_auto(monkeypatch):
    """local_timeout_profile = 'slow'/'fast' forces the profile regardless of
    where the model runs; unknown values fall back to auto."""
    slow = _brain_with_settings(monkeypatch, {"local_timeout_profile": "slow"})
    monkeypatch.setattr(slow, "_get_json", lambda url: _fake_ps(99))
    assert slow.active_profile() == "slow"
    assert slow.response_timeout() == 600.0

    fast = _brain_with_settings(monkeypatch, {"local_timeout_profile": "fast"})
    monkeypatch.setattr(fast, "_get_json", lambda url: _fake_ps(0))
    assert fast.active_profile() == "fast"
    assert fast.response_timeout() == 180.0

    weird = _brain_with_settings(monkeypatch, {"local_timeout_profile": "turbo"})
    assert weird._timeout_profile == "auto"


def test_cpu_resident_from_ps_parsing():
    """The /api/ps interpreter: VRAM==0 means CPU; anything unclear means
    'unknown' (None), never a guessed slow/fast."""
    assert _cpu_resident_from_ps(_fake_ps(0), "test-model") is True
    assert _cpu_resident_from_ps(_fake_ps(5), "test-model") is False
    # Tag variants match by prefix in either direction.
    assert _cpu_resident_from_ps(_fake_ps(0, "test-model:latest"), "test-model") is True
    assert _cpu_resident_from_ps(_fake_ps(0, "test-model"), "test-model:latest") is True
    # A single loaded model is unambiguous even under a different tag.
    assert _cpu_resident_from_ps(_fake_ps(0, "other:7b"), "unrelated") is True
    # Multiple models and none matching -> unknown.
    multi = {"models": [{"name": "a", "size_vram": 0}, {"name": "b", "size_vram": 0}]}
    assert _cpu_resident_from_ps(multi, "c") is None
    # Unknown shapes -> unknown.
    assert _cpu_resident_from_ps(None, "test-model") is None
    assert _cpu_resident_from_ps({"models": "nope"}, "test-model") is None
    assert _cpu_resident_from_ps({"models": []}, "test-model") is None
    assert _cpu_resident_from_ps({"models": [{"name": "test-model"}]}, "test-model") is None
    assert _cpu_resident_from_ps(
        {"models": [{"name": "test-model", "size_vram": "lots"}]}, "test-model"
    ) is None


def test_ps_probe_cached_within_ttl(monkeypatch):
    """The probe runs at most once per endpoint+model per TTL: a tool loop's
    follow-up calls must not re-probe on every turn."""
    brain = _brain_with_settings(monkeypatch, {})
    calls = []

    def fake_get(url):
        calls.append(url)
        return _fake_ps(0)

    monkeypatch.setattr(brain, "_get_json", fake_get)

    assert brain.active_profile() == "slow"
    assert brain.active_profile() == "slow"
    assert calls == ["http://test-host:11434/api/ps"]


def test_open_stream_uses_profile_first_token_budget(monkeypatch):
    """The stream socket opens with the profile's first-token budget (prefill
    included), not the inter-chunk budget."""
    brain = _brain_with_settings(monkeypatch, {})
    monkeypatch.setattr(brain, "_model_is_cpu_resident", lambda: True)
    captured = {}

    class _Ctx:
        def __enter__(self):
            return _FakeStream([])

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        return _Ctx()

    monkeypatch.setattr(local_brain_mod.urllib.request, "urlopen", fake_urlopen)
    req = local_brain_mod.urllib.request.Request(
        "http://test-host:11434/v1/chat/completions", data=b"{}"
    )
    brain._open_stream(req)

    assert captured["timeout"] == TIMEOUT_PROFILES["slow"]["first_token"] == 600.0


class _FakeSock:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)


class _FakeStream:
    """Minimal stand-in for http.client's streaming response."""

    def __init__(self, lines):
        self._lines = list(lines)
        self.sock = _FakeSock()
        self.fp = types.SimpleNamespace(
            raw=types.SimpleNamespace(_sock=self.sock)
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)


def test_stream_tightens_to_idle_after_first_byte(monkeypatch):
    """Once the first chunk arrives (prefill over), the socket drops from the
    first-token budget to the tight inter-chunk budget, so a stream that
    stalls mid-answer still fails fast."""
    brain = _brain_with_settings(monkeypatch, {})
    stream = _FakeStream(
        [b'data: {"choices": [{"delta": {"content": "hi"}}]}\n']
    )
    monkeypatch.setattr(brain, "_open_stream", lambda req: stream)

    chunks = list(brain.generate_chat_stream([{"role": "user", "content": "hi"}]))

    assert chunks == [{"choices": [{"delta": {"content": "hi"}}]}]
    assert stream.sock.timeouts == [STREAM_IDLE_TIMEOUT]


def test_stream_tightening_failure_leaves_first_token_budget(monkeypatch):
    """The socket switch is a CPython implementation detail; if it is not
    reachable the stream simply keeps the (still bounded) first-token
    budget instead of raising."""
    brain = _brain_with_settings(monkeypatch, {})
    stream = _FakeStream([b'data: {"choices": [{"delta": {"content": "hi"}}]}\n'])
    stream.fp = None  # simulate a response object without the expected shape
    monkeypatch.setattr(brain, "_open_stream", lambda req: stream)

    chunks = list(brain.generate_chat_stream([{"role": "user", "content": "hi"}]))

    assert len(chunks) == 1  # the turn still completes, no exception


# ------------------------------------------- gateway local-link consistency


def test_local_read_timeout_borrows_brain_profile(monkeypatch):
    """The gateway's local link uses LocalBrain's resolved budget, so memory
    extraction / intent parsing on a CPU model gets the same patience as the
    reply path instead of a second, conflicting constant."""
    from core.local_brain import local_brain as brain_singleton

    monkeypatch.setattr(brain_singleton, "response_timeout", lambda: 600.0)
    assert llm_client_mod._local_read_timeout() == (
        llm_client_mod.REQUEST_TIMEOUT[0],
        600.0,
    )


def test_local_read_timeout_falls_back_on_error(monkeypatch):
    """If the brain cannot resolve a budget, the client keeps its own."""
    from core.local_brain import local_brain as brain_singleton

    def boom():
        raise RuntimeError("no brain")

    monkeypatch.setattr(brain_singleton, "response_timeout", boom)
    assert llm_client_mod._local_read_timeout() == llm_client_mod.REQUEST_TIMEOUT
