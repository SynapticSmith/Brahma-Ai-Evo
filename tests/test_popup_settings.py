"""Popup timers: one cosmetic group, one separate approval budget.

The parsing lives in ``core.popup_settings`` so the task card, the briefing and
the approval banner cannot disagree about what a blank/zero/malformed value
means. Approval is deliberately not covered by the cosmetic toggle.
"""
from __future__ import annotations

import core.popup_settings as popup


def _settings(monkeypatch, values):
    monkeypatch.setattr(popup, "_settings", lambda: dict(values))


def test_defaults_are_per_surface(monkeypatch):
    _settings(monkeypatch, {})

    assert popup.auto_dismiss_enabled() is True
    assert popup.dismiss_seconds(popup.DEFAULT_TASK_CARD_SECONDS) == 4.0
    assert popup.dismiss_seconds(popup.DEFAULT_BRIEFING_SECONDS) == 30.0


def test_zero_means_the_surface_default(monkeypatch):
    _settings(monkeypatch, {"popup_dismiss_seconds": 0})

    assert popup.dismiss_seconds(popup.DEFAULT_TASK_CARD_SECONDS) == 4.0
    assert popup.dismiss_seconds(popup.DEFAULT_BRIEFING_SECONDS) == 30.0


def test_an_explicit_duration_overrides_both_surfaces(monkeypatch):
    _settings(monkeypatch, {"popup_dismiss_seconds": 12})

    assert popup.dismiss_seconds(popup.DEFAULT_TASK_CARD_SECONDS) == 12.0
    assert popup.dismiss_seconds(popup.DEFAULT_BRIEFING_SECONDS) == 12.0


def test_a_malformed_duration_falls_back_to_the_surface_default(monkeypatch):
    _settings(monkeypatch, {"popup_dismiss_seconds": "not a number"})

    assert popup.dismiss_seconds(popup.DEFAULT_TASK_CARD_SECONDS) == 4.0


def test_the_duration_is_capped(monkeypatch):
    _settings(monkeypatch, {"popup_dismiss_seconds": 99999})

    assert popup.dismiss_seconds(4.0) == popup.MAX_DISMISS_SECONDS


def test_disabling_auto_dismiss_keeps_cosmetic_popups_open(monkeypatch):
    _settings(
        monkeypatch,
        {"popup_auto_dismiss_enabled": False, "popup_dismiss_seconds": 5},
    )

    assert popup.dismiss_seconds(popup.DEFAULT_TASK_CARD_SECONDS) is None
    assert popup.dismiss_seconds(popup.DEFAULT_BRIEFING_SECONDS) is None


def test_the_toggle_does_not_disable_the_approval_budget(monkeypatch):
    _settings(
        monkeypatch,
        {"popup_auto_dismiss_enabled": False, "approval_timeout_seconds": 45},
    )

    assert popup.approval_timeout() == 45.0


def test_the_approval_budget_is_clamped_to_a_finite_range(monkeypatch):
    _settings(monkeypatch, {"approval_timeout_seconds": 1})
    assert popup.approval_timeout() == popup.APPROVAL_MIN_SECONDS

    _settings(monkeypatch, {"approval_timeout_seconds": 99999})
    assert popup.approval_timeout() == popup.APPROVAL_MAX_SECONDS

    _settings(monkeypatch, {"approval_timeout_seconds": "nope"})
    assert popup.approval_timeout() == popup.APPROVAL_DEFAULT_SECONDS


def test_an_explicit_approval_timeout_is_capped_but_not_floored(monkeypatch):
    _settings(monkeypatch, {})

    # A caller (the API's limits.approval_timeout_s) may deliberately use a
    # short budget; it still cannot ask for an unbounded prompt.
    assert popup.clamp_approval_timeout(3) == 3.0
    assert popup.clamp_approval_timeout(99999) == popup.APPROVAL_MAX_SECONDS
    assert popup.clamp_approval_timeout(None) == popup.approval_timeout()


def test_the_confirm_module_uses_the_clamped_budget():
    import core.confirm as confirm

    pending = confirm._Pending(
        key="run_command",
        title="Approve run_command?",
        detail="x",
        run=lambda: "ok",
        at=0.0,
        waiter=None,
    )
    assert pending.timeout == confirm.TIMEOUT_SECONDS
