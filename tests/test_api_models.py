"""Model-id grammar, lane/profile intersection, and /v1/models rows."""

from __future__ import annotations

import pytest

from api_support import make_config, principal
from brahma_api import models
from brahma_api.errors import ApiError


@pytest.mark.parametrize(
    "raw, variant, lane, alias",
    [
        ("brahma-evo", "", "agent", None),
        ("brahma-evo:read", "read", "agent", None),
        ("brahma-evo:plan", "plan", "plan", None),
        ("brahma-evo:read@test", "read", "agent", "test"),
        ("  brahma-evo  ", "", "agent", None),
    ],
)
def test_parse_accepts_the_grammar(raw, variant, lane, alias):
    parsed = models.parse_model_id(raw)
    assert parsed is not None
    assert (parsed.variant, parsed.lane, parsed.workspace_alias) == (variant, lane, alias)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "gpt-4o",
        "brahma-evo:",
        "brahma-evo:shell",
        "brahma-evo:read@bad alias",
        "brahma-evo@a@b",
        "other:read",
    ],
)
def test_parse_rejects_anything_else(raw):
    assert models.parse_model_id(raw) is None


def test_unknown_and_unimplemented_models_are_the_same_404(tmp_path):
    config = make_config(tmp_path)
    for raw in ("gpt-4o", "brahma-evo:shell", "brahma-evo:passthrough", "brahma-evo:hybrid"):
        with pytest.raises(ApiError) as caught:
            models.resolve(config, principal("reader"), raw)
        assert caught.value.status == 404
        assert caught.value.code == "model_not_found"


def test_reader_keeps_tools_and_the_workspace(tmp_path):
    config = make_config(tmp_path)
    resolved = models.resolve(config, principal("reader"), "brahma-evo")
    assert resolved.profile == "reader"
    assert resolved.tools is True
    assert resolved.workspace == config.workspace_path("test")
    assert resolved.max_turns == config.limits.max_turns


def test_a_token_can_only_narrow_its_profile(tmp_path):
    config = make_config(tmp_path)
    downgraded = models.resolve(config, principal("worker"), "brahma-evo:read")
    assert downgraded.profile == "reader"

    upgraded = models.resolve(config, principal("reader"), "brahma-evo")
    assert upgraded.profile == "reader"


def test_chat_is_tool_free_whatever_the_model_says(tmp_path):
    config = make_config(tmp_path)
    for raw in ("brahma-evo", "brahma-evo:read"):
        resolved = models.resolve(config, principal("chat"), raw)
        assert resolved.profile == "chat"
        assert resolved.tools is False
        assert resolved.workspace is None
        assert resolved.workspace_alias is None

    # A chat key is bound to no workspace, so naming one is a widening attempt.
    with pytest.raises(ApiError) as caught:
        models.resolve(config, principal("chat"), "brahma-evo@test")
    assert caught.value.status == 403


def test_plan_runs_one_turn_without_tools(tmp_path):
    config = make_config(tmp_path)
    resolved = models.resolve(config, principal("reader"), "brahma-evo:plan")
    assert resolved.lane == "plan"
    assert resolved.tools is False
    assert resolved.max_turns == 1


def test_a_key_cannot_select_another_workspace(tmp_path):
    config = make_config(tmp_path)
    with pytest.raises(ApiError) as caught:
        models.resolve(config, principal("reader"), "brahma-evo@other")
    assert caught.value.status == 403
    assert caught.value.code == "insufficient_scope"


def test_an_unconfigured_alias_is_refused(tmp_path):
    config = make_config(tmp_path)
    with pytest.raises(ApiError) as caught:
        models.resolve(config, principal("reader", workspace="gone"), "brahma-evo")
    assert caught.value.status == 403
    assert caught.value.code == "workspace_not_allowed"


def test_model_rows_are_filtered_by_the_key(tmp_path):
    config = make_config(tmp_path)
    reader_rows = models.model_rows(config, principal("reader"))
    chat_rows = models.model_rows(config, principal("chat"))

    assert [row["id"] for row in reader_rows] == list(models.LISTED_MODELS)
    assert all(row["brahma"]["tools"] is True for row in reader_rows[:2])
    assert reader_rows[2]["brahma"]["lane"] == "plan"
    assert reader_rows[2]["brahma"]["tools"] is False

    assert all(row["brahma"]["tools"] is False for row in chat_rows)
    assert all(row["brahma"]["workspace"] is None for row in chat_rows)


@pytest.mark.parametrize(
    "profile, tool_class, expected",
    [
        ("chat", "read", "deny"),
        ("reader", "read", "allow"),
        ("reader", "write", "deny"),
        ("worker", "read", "allow"),
        ("worker", "write", "allow"),
        ("worker", "exec", "deny"),
        ("worker", "network", "allow"),
        ("worker", "device", "deny"),
    ],
)
def test_profiles_are_policy_documents(tmp_path, profile, tool_class, expected):
    policy = models.profile_policy(profile)
    decision = policy.evaluate(
        "some_tool",
        {"path": str(tmp_path / "x.txt")},
        tool_class=tool_class,
        context_workspace=tmp_path,
        headless=True,
    )
    assert decision.decision == expected


def test_worker_trusts_the_configured_workspace(tmp_path):
    policy = models.profile_policy("worker")
    assert policy.data["paths"]["untrusted_workspace"] == "allow"
    assert policy.data["paths"]["outside_workspace"] == "deny"


def test_fingerprint_is_stable_and_short(tmp_path):
    first = models.policy_fingerprint(models.profile_policy("reader"))
    second = models.policy_fingerprint(models.profile_policy("reader"))
    assert first == second
    assert len(first) == 8
