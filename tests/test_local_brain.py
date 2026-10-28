"""Unit tests for core.local_brain -- the offline endpoint contract (log sections 16-17).

What these pin down:

* the context-budget contract from the system-awareness prompt rewrite:
  ``chat_complete`` and the streaming path must request an explicit
  ``num_ctx``. Ollama's OpenAI-compatible endpoint silently truncates to its
  own default (2048-4096 tokens), which is smaller than the system prompt the
  app actually sends (core/prompt.txt is ~15KB on its own), so without the
  budget the offline 3B model would lose its operating instructions before
  the user's turn.
* the hardware-aware timeout-profile contract: a CPU-resident or partially
  offloaded model gets the slow budgets (prefill of the ~4K-token context can
  take minutes), a *fully* VRAM-resident one earns the fast budgets, and
  every inconclusive outcome - cold start before Ollama has lazily loaded
  the target model, broken probe, non-Ollama server - resolves to the slow
  budgets too: patience is the safe default for a local model, "fast" is
  earned never assumed. An explicit Settings value beats the auto-detection
  and both budgets stay finite so a wedged server still becomes a surfaced
  error instead of a hang. The gateway's local link borrows the same
  budget (llm_client._local_read_timeout) instead of a second constant.
* the tool-capability contract: a model whose runtime rejects the tools
  array ("does not support tools" - e.g. gemma2 on Ollama, which has no
  tool template) must not kill the turn; the verdict is remembered and the
  turn retried once without tools, while every unrelated 400 keeps
  propagating.

No network: ``_post_json`` / ``_open_stream`` / ``_get_json`` are replaced
with recorders.
"""

import json
import types

import llm_client as llm_client_mod
from core import local_brain as local_brain_mod
from core.local_brain import (
    LOCAL_NUM_CTX,
    STREAM_IDLE_TIMEOUT,
    TIMEOUT_PROFILES,
    LocalBrain,
    LocalBrainError,
    _ps_load_speed,
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


def _fake_ps(size_vram, name="test-model:latest", size=None):
    entry = {"name": name, "size_vram": size_vram}
    if size is not None:
        entry["size"] = size
    return {"models": [entry]}


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
    whole-body budget, not the fast one."""
    brain = _brain_with_settings(monkeypatch, {})
    monkeypatch.setattr(brain, "_get_json", lambda url: _fake_ps(0))
    captured = _capture_timeout(monkeypatch, brain)

    brain.chat_complete(messages=[{"role": "user", "content": "hi"}])

    assert brain.active_profile() == "slow"
    assert captured["timeout"] == TIMEOUT_PROFILES["slow"]["response"]
    assert TIMEOUT_PROFILES["slow"]["response"] > TIMEOUT_PROFILES["fast"]["response"]


def test_vram_resident_model_keeps_fast_budgets(monkeypatch):
    """A fully offloaded load (VRAM ~= model size) is GPU-fast: it keeps the
    historical fast budget, which is also the better failure bound."""
    brain = _brain_with_settings(monkeypatch, {})
    monkeypatch.setattr(
        brain, "_get_json", lambda url: _fake_ps(8_000_000_000, size=8_000_000_000)
    )
    captured = _capture_timeout(monkeypatch, brain)

    brain.chat_complete(messages=[{"role": "user", "content": "hi"}])

    assert brain.active_profile() == "fast"
    assert captured["timeout"] == TIMEOUT_PROFILES["fast"]["response"]


def test_partial_offload_gets_slow_budgets(monkeypatch):
    """A model only partially offloaded still has CPU-resident layers, and
    those gate prompt prefill - so it must get the patient budget, not the
    fast one. (Section-17 code treated *any* VRAM as fast.)"""
    brain = _brain_with_settings(monkeypatch, {})
    monkeypatch.setattr(
        brain, "_get_json", lambda url: _fake_ps(2_000_000_000, size=8_000_000_000)
    )
    captured = _capture_timeout(monkeypatch, brain)

    brain.chat_complete(messages=[{"role": "user", "content": "hi"}])

    assert brain.active_profile() == "slow"
    assert captured["timeout"] == TIMEOUT_PROFILES["slow"]["response"]


def test_probe_failure_resolves_to_slow_and_is_cached(monkeypatch):
    """A failed probe (non-Ollama server, wedged runtime) resolves to the
    patient budget - for a local model, slow is the safe default - and the
    failure is cached so we do not re-fail on every single call."""
    brain = _brain_with_settings(monkeypatch, {})
    calls = []

    def boom(url):
        calls.append(url)
        raise LocalBrainError("probe failed")

    monkeypatch.setattr(brain, "_get_json", boom)
    captured = _capture_timeout(monkeypatch, brain)

    brain.chat_complete(messages=[{"role": "user", "content": "hi"}])

    assert brain.active_profile() == "slow"
    assert captured["timeout"] == TIMEOUT_PROFILES["slow"]["response"]
    # One probe only: the failure is remembered for the TTL.
    brain.active_profile()
    assert len(calls) == 1


def test_settings_profile_override_beats_auto(monkeypatch):
    """local_timeout_profile = 'slow'/'fast' forces the profile regardless of
    where the model runs; unknown values fall back to auto."""
    slow = _brain_with_settings(monkeypatch, {"local_timeout_profile": "slow"})
    monkeypatch.setattr(slow, "_get_json", lambda url: _fake_ps(99))
    assert slow.active_profile() == "slow"
    assert slow.response_timeout() == TIMEOUT_PROFILES["slow"]["response"]

    fast = _brain_with_settings(monkeypatch, {"local_timeout_profile": "fast"})
    monkeypatch.setattr(fast, "_get_json", lambda url: _fake_ps(0))
    assert fast.active_profile() == "fast"
    assert fast.response_timeout() == TIMEOUT_PROFILES["fast"]["response"]

    weird = _brain_with_settings(monkeypatch, {"local_timeout_profile": "turbo"})
    assert weird._timeout_profile == "auto"


def test_ps_load_speed_parsing():
    """The /api/ps interpreter: fully VRAM-resident means fast, zero VRAM or
    partial offload means slow, and anything unclear means unknown (None) so
    active_profile can default it to the patient budget."""
    assert _ps_load_speed(_fake_ps(0), "test-model") == "slow"
    assert _ps_load_speed(
        _fake_ps(8_000_000_000, size=8_000_000_000), "test-model"
    ) == "fast"
    # Partial offload: the CPU-resident layers gate prefill.
    assert _ps_load_speed(
        _fake_ps(2_000_000_000, size=8_000_000_000), "test-model"
    ) == "slow"
    # Tag variants match by prefix in either direction.
    assert _ps_load_speed(_fake_ps(0, "test-model:latest"), "test-model") == "slow"
    assert _ps_load_speed(_fake_ps(0, "test-model"), "test-model:latest") == "slow"
    # A single loaded model is unambiguous even under a different tag.
    assert _ps_load_speed(_fake_ps(0, "other:7b"), "unrelated") == "slow"
    # Cold start: the target model is not loaded yet (Ollama loads lazily on
    # the first request) -> unknown, never a fast verdict.
    assert _ps_load_speed({"models": []}, "test-model") is None
    # Multiple models and none matching -> unknown.
    multi = {"models": [{"name": "a", "size_vram": 0}, {"name": "b", "size_vram": 0}]}
    assert _ps_load_speed(multi, "c") is None
    # Unknown shapes -> unknown.
    assert _ps_load_speed(None, "test-model") is None
    assert _ps_load_speed({"models": "nope"}, "test-model") is None
    assert _ps_load_speed({"models": [{"name": "test-model"}]}, "test-model") is None
    assert _ps_load_speed(
        {"models": [{"name": "test-model", "size_vram": "lots"}]}, "test-model"
    ) is None
    # VRAM reported but the total unknown -> could be partial, so no fast.
    assert _ps_load_speed(_fake_ps(5), "test-model") is None


def test_cold_start_resolves_to_slow_and_reprobes(monkeypatch):
    """The reported field failure: on the first turn after startup the target
    model is not in /api/ps yet, so the probe is inconclusive - it must
    resolve to the patient (slow) budget, never the fast one, and the
    inconclusive verdict must NOT be cached, so the next turn can find the
    model loaded."""
    brain = _brain_with_settings(monkeypatch, {})
    calls = []

    def fake_get(url):
        calls.append(url)
        return {"models": []}

    monkeypatch.setattr(brain, "_get_json", fake_get)

    assert brain.active_profile() == "slow"  # probe 1
    assert brain.response_timeout() == TIMEOUT_PROFILES["slow"]["response"]  # probe 2
    assert brain.first_token_timeout() == TIMEOUT_PROFILES["slow"]["first_token"]  # probe 3
    assert brain.active_profile() == "slow"  # probe 4
    # Uncached inconclusive verdict: every resolution re-probes, so the turn
    # after Ollama lazily loads the model sees the real residency.
    assert len(calls) == 4


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
    monkeypatch.setattr(brain, "_probe_load_speed", lambda: "slow")
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

    assert captured["timeout"] == TIMEOUT_PROFILES["slow"]["first_token"]


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


# ----------------------------------------------- tool-capability fallback

# The real Ollama rejection, verbatim from the field: gemma2 (and other
# models without a tool template)400 on any tools-bearing request.
_TOOLS_UNSUPPORTED_400 = (
    "http://localhost:11434/v1/chat/completions: HTTP 400 Bad Request "
    '{"error":{"message":"registry.ollama.ai/library/gemma2:2b does not '
    'support tools","type":"invalid_request_error","param":null,"code":null}}'
)

_GEMMA_TOOLS = [
    {
        "name": "open_app",
        "description": "Opens an application.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"app_name": {"type": "STRING"}},
        },
    }
]


def test_tools_unsupported_400_retries_without_tools(monkeypatch):
    """A runtime rejection of the tools array (no tool template) must not
    kill the turn: the verdict is remembered and the same turn retried once
    without tools, so the model still answers."""
    brain = _brain_with_settings(monkeypatch, {})
    calls = []

    def fake_post(payload, timeout):
        calls.append(dict(payload))  # snapshot: the retry pops keys in place
        if "tools" in payload:
            raise LocalBrainError(_TOOLS_UNSUPPORTED_400)
        return '{"choices": [{"message": {"content": "Here is what I can do."}}]}'

    monkeypatch.setattr(brain, "_post_json", fake_post)
    res = brain.chat_complete(
        messages=[{"role": "user", "content": "list my tools"}], tools=_GEMMA_TOOLS
    )

    assert len(calls) == 2, "expected the tools attempt, then one tool-less retry"
    assert "tools" in calls[0]
    assert "tools" not in calls[1]
    assert res["choices"][0]["message"]["content"].startswith("Here")
    assert brain._is_toolless("test-model")


def test_remembered_toolless_model_skips_tools_on_later_turns(monkeypatch):
    """The learning must stick: later turns never send the doomed tools
    array again - no wasted round-trip, no 400 on every message."""
    brain = _brain_with_settings(monkeypatch, {})
    brain._mark_toolless("test-model")
    captured = {}

    def fake_post(payload, timeout):
        captured["payload"] = payload
        return '{"choices": [{"message": {"content": "ok"}}]}'

    monkeypatch.setattr(brain, "_post_json", fake_post)
    brain.chat_complete(
        messages=[{"role": "user", "content": "hi"}], tools=_GEMMA_TOOLS
    )

    assert "tools" not in captured["payload"]


def test_other_models_still_get_tools_after_one_is_marked(monkeypatch):
    """The verdict is per endpoint+model, not global: selecting a
    tool-capable model next keeps its tool surface."""
    brain = _brain_with_settings(monkeypatch, {})
    brain._mark_toolless("gemma2:2b")
    captured = {}

    def fake_post(payload, timeout):
        captured["payload"] = payload
        return '{"choices": [{"message": {"content": "ok"}}]}'

    monkeypatch.setattr(brain, "_post_json", fake_post)
    brain.chat_complete(
        messages=[{"role": "user", "content": "hi"}],
        model="qwen2.5:3b",
        tools=_GEMMA_TOOLS,
    )

    assert "tools" in captured["payload"]


def test_unrelated_400_still_raises(monkeypatch):
    """Only the specific tool-template rejection is absorbed; every other
    error keeps propagating to the reply ladder, with no silent retry."""
    brain = _brain_with_settings(monkeypatch, {})
    calls = []

    def fake_post(payload, timeout):
        calls.append(payload)
        raise LocalBrainError(
            'http://x: HTTP 400 Bad Request {"error":{"message":"invalid format"}}'
        )

    monkeypatch.setattr(brain, "_post_json", fake_post)

    try:
        brain.chat_complete(
            messages=[{"role": "user", "content": "hi"}], tools=_GEMMA_TOOLS
        )
    except LocalBrainError as exc:
        assert "invalid format" in str(exc)
    else:
        raise AssertionError("expected LocalBrainError for an unrelated 400")
    assert len(calls) == 1, "a real error must not be retried away"


def test_stream_skips_tools_for_remembered_toolless_model(monkeypatch):
    """The streaming path must not send a doomed tools array either."""
    brain = _brain_with_settings(monkeypatch, {})
    brain._mark_toolless("test-model")
    captured = {}

    class _Ctx:
        def __enter__(self):
            return _FakeStream([])

        def __exit__(self, *exc):
            return False

    def fake_open(req):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _Ctx()

    monkeypatch.setattr(brain, "_open_stream", fake_open)
    list(
        brain.generate_chat_stream(
            [{"role": "user", "content": "hi"}], tools=_GEMMA_TOOLS
        )
    )

    assert "tools" not in captured["payload"]
