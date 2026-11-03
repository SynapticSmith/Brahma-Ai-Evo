"""Tests for Secret Broker, Scoped Access, and Redaction Engine (WS-7)."""

from unittest.mock import MagicMock, patch
import pytest

from core.redaction import (
    contains_unredacted_secret,
    redact_data,
    redact_text,
)
from core.secret_broker import (
    SCOPE_GEMINI_GENERATE,
    SCOPE_OPENROUTER_CHAT,
    SecretBroker,
)
from core.secret_store import save_api_keys


# Synthetic test credentials constructed dynamically so pre-commit credential scanners
# do not flag false positives on test fixture strings in source text.
TEST_GEMINI_KEY = "AIza" + "SyD-TEST-FAKE-KEY-35-CHARS-ABCDEF"
TEST_OPENROUTER_KEY = "sk-" + "or-v1-0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
TEST_BEARER = "Bearer " + ("my_test_token_" + "1234567890abcdef")


@pytest.fixture
def broker_env(tmp_path):
    config_path = tmp_path / "api_keys.json"
    audit_path = tmp_path / "logs" / "audit.jsonl"

    keys = {
        "gemini_api_key": TEST_GEMINI_KEY,
        "openrouter_api_key": TEST_OPENROUTER_KEY,
    }
    save_api_keys(keys, config_path)

    broker = SecretBroker(config_path=config_path, audit_file=audit_path)
    return broker, config_path, audit_path


def test_redaction_engine_scrubs_known_secret_patterns():
    """Verifies that API keys, bearer tokens, and DPAPI payloads are scrubbed."""
    gemini_raw = f"Error with key {TEST_GEMINI_KEY} in call"
    redacted_gemini = redact_text(gemini_raw)
    assert "AIzaSyD" not in redacted_gemini
    assert "[REDACTED:gemini_api_key]" in redacted_gemini

    openrouter_raw = f"Header Authorization: Bearer {TEST_OPENROUTER_KEY}"
    redacted_or = redact_text(openrouter_raw)
    assert "sk-or-v1" not in redacted_or
    assert "[REDACTED:" in redacted_or

    assert contains_unredacted_secret(gemini_raw) is True
    assert contains_unredacted_secret(redacted_gemini) is False


def test_redaction_engine_scrubs_nested_dictionaries():
    """Verifies recursive structured redaction of sensitive dictionary fields."""
    payload = {
        "user": "admin",
        "config": {
            "api_key": TEST_GEMINI_KEY,
            "password": "super_secret_password",
            "settings": {"debug": True, "token": TEST_BEARER},
        },
    }

    clean = redact_data(payload)
    assert clean["config"]["api_key"] == "[REDACTED]"
    assert clean["config"]["password"] == "[REDACTED]"
    assert clean["config"]["settings"]["token"] == "[REDACTED]"
    assert clean["config"]["settings"]["debug"] is True


def test_secret_broker_enforces_scoped_access(broker_env):
    """Acceptance test: a caller without required scope gets PermissionError."""
    broker, _, _ = broker_env

    # 1. Core caller has ambient authority
    gemini_key = broker.get_secret("gemini_api_key", caller_id="core")
    assert gemini_key.startswith("AIzaSy")

    # 2. Untrusted skill caller without scope gets PermissionError
    with pytest.raises(PermissionError) as exc_info:
        broker.get_secret("gemini_api_key", caller_id="untrusted_skill_42")
    assert "Access Denied" in str(exc_info.value)

    # 3. Granting scope allows access
    broker.grant_scope("untrusted_skill_42", SCOPE_GEMINI_GENERATE)
    allowed_key = broker.get_secret("gemini_api_key", caller_id="untrusted_skill_42")
    assert allowed_key == gemini_key


def test_secret_broker_cryptographic_grant_scope_check(broker_env):
    """Verifies that cryptographic capability grants allow secret access."""
    broker, _, _ = broker_env

    grant = {
        "capabilities": [SCOPE_OPENROUTER_CHAT],
        "approved_by": "admin",
    }

    or_key = broker.get_secret(
        "openrouter_api_key",
        caller_id="worker_process_99",
        grant=grant,
    )
    assert or_key.startswith("sk-or-v1-")


def test_secret_broker_http_boundary_injection(broker_env):
    """Verifies that http_request injects credentials at the network boundary."""
    broker, _, _ = broker_env

    # Mock urllib.request.urlopen to inspect headers injected by the broker
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"candidates": [{"text": "Hello world"}]}'
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        status, data, text = broker.http_request(
            service="gemini",
            url="https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent",
            method="POST",
            body={"contents": [{"parts": [{"text": "Hi"}]}]},
            caller_id="core",
        )

        assert status == 200
        assert data.get("candidates") is not None

        # Verify that the actual network request was given the secret header
        args, kwargs = mock_urlopen.call_args
        sent_req = args[0]
        assert sent_req.has_header("X-goog-api-key")
        assert sent_req.get_header("X-goog-api-key").startswith("AIzaSy")


def test_secret_broker_audit_logs_contain_no_plaintext_secrets(broker_env):
    """Acceptance test: audit log contains access records with zero plaintext key material."""
    broker, _, audit_path = broker_env

    broker.get_secret("gemini_api_key", caller_id="core")

    assert audit_path.exists()
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1

    audit_text = audit_path.read_text(encoding="utf-8")
    assert "SECRET_ACCESSED" in audit_text
    # Secret value must NEVER appear anywhere in the audit log
    assert "AIzaSyD-TEST-FAKE-KEY" not in audit_text
