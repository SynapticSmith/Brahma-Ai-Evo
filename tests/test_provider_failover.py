"""Unit tests for the Gemini→OpenRouter failover timing policy (P4-19 seam)."""

from core.provider_failover import DEFAULT_COOLDOWN_SECONDS, ProviderFailover


def test_starts_preferring_gemini():
    assert ProviderFailover().prefer_openrouter(now=0.0) is False


def test_throttle_routes_to_openrouter_during_cooldown():
    f = ProviderFailover()
    f.throttle_to_openrouter(now=0.0)
    assert f.prefer_openrouter(now=0.0) is True
    assert f.prefer_openrouter(now=DEFAULT_COOLDOWN_SECONDS - 1) is True


def test_cooldown_expires():
    f = ProviderFailover()
    f.throttle_to_openrouter(now=0.0)
    assert f.prefer_openrouter(now=DEFAULT_COOLDOWN_SECONDS) is False


def test_explicit_window_overrides_default():
    f = ProviderFailover()
    f.throttle_to_openrouter(seconds=10.0, now=0.0)
    assert f.prefer_openrouter(now=9.9) is True
    assert f.prefer_openrouter(now=10.0) is False


def test_reset_clears_throttle():
    f = ProviderFailover()
    f.throttle_to_openrouter(now=0.0)
    f.reset()
    assert f.prefer_openrouter(now=0.0) is False
