"""
Tests for parallax_engine.auth

Covers:
  - configure_credentials() → 'cloud-provider' when CLAUDE_CODE_USE_* is set
  - configure_credentials() → 'api-key' when ANTHROPIC_API_KEY is set
  - configure_credentials() → 'oauth' when CLAUDE_CODE_OAUTH_TOKEN is set
  - OAuth maps token to ANTHROPIC_API_KEY envvar
  - is_cloud_provider() helper
  - effective_api_key() helper
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

from parallax_engine.auth import (
    _API_KEY_VAR,
    _CLOUD_PROVIDER_VARS,
    _OAUTH_TOKEN_VAR,
    configure_credentials,
    effective_api_key,
    is_cloud_provider,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_env() -> dict[str, str | None]:
    """Snapshot relevant env vars so we can restore them."""
    keys = [_API_KEY_VAR, _OAUTH_TOKEN_VAR] + list(_CLOUD_PROVIDER_VARS)
    return {k: os.environ.get(k) for k in keys}


def _remove_auth_vars() -> None:
    """Remove all auth-related env vars from the current process."""
    keys = [_API_KEY_VAR, _OAUTH_TOKEN_VAR] + list(_CLOUD_PROVIDER_VARS)
    for k in keys:
        os.environ.pop(k, None)


# ---------------------------------------------------------------------------
# configure_credentials() — cloud-provider path
# ---------------------------------------------------------------------------


class TestConfigureCredentialsCloudProvider:
    def test_bedrock_returns_cloud_provider(self, monkeypatch):
        _remove_auth_vars()
        monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
        assert configure_credentials() == "cloud-provider"

    def test_vertex_returns_cloud_provider(self, monkeypatch):
        _remove_auth_vars()
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "true")
        assert configure_credentials() == "cloud-provider"

    def test_foundry_returns_cloud_provider(self, monkeypatch):
        _remove_auth_vars()
        monkeypatch.setenv("CLAUDE_CODE_USE_FOUNDRY", "1")
        assert configure_credentials() == "cloud-provider"

    def test_cloud_provider_takes_precedence_over_api_key(self, monkeypatch):
        _remove_auth_vars()
        monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
        monkeypatch.setenv(_API_KEY_VAR, "sk-test-key")
        assert configure_credentials() == "cloud-provider"


# ---------------------------------------------------------------------------
# configure_credentials() — api-key path
# ---------------------------------------------------------------------------


class TestConfigureCredentialsApiKey:
    def test_api_key_returns_api_key(self, monkeypatch):
        _remove_auth_vars()
        monkeypatch.setenv(_API_KEY_VAR, "sk-ant-key-12345")
        assert configure_credentials() == "api-key"

    def test_api_key_takes_precedence_over_oauth(self, monkeypatch):
        _remove_auth_vars()
        monkeypatch.setenv(_API_KEY_VAR, "sk-ant-key-12345")
        monkeypatch.setenv(_OAUTH_TOKEN_VAR, "oauth-token-xyz")
        assert configure_credentials() == "api-key"


# ---------------------------------------------------------------------------
# configure_credentials() — oauth path
# ---------------------------------------------------------------------------


class TestConfigureCredentialsOAuth:
    def test_oauth_returns_oauth(self, monkeypatch):
        _remove_auth_vars()
        monkeypatch.setenv(_OAUTH_TOKEN_VAR, "oauth-tok-abcdef")
        result = configure_credentials()
        assert result == "oauth"

    def test_oauth_maps_token_to_api_key_envvar(self, monkeypatch):
        _remove_auth_vars()
        token = "oauth-tok-mapped"
        monkeypatch.setenv(_OAUTH_TOKEN_VAR, token)
        configure_credentials()
        assert os.environ.get(_API_KEY_VAR) == token

    def test_oauth_does_not_overwrite_existing_api_key(self, monkeypatch):
        """When ANTHROPIC_API_KEY is already set, oauth path is not reached."""
        _remove_auth_vars()
        monkeypatch.setenv(_API_KEY_VAR, "real-key")
        monkeypatch.setenv(_OAUTH_TOKEN_VAR, "oauth-tok")
        configure_credentials()
        assert os.environ.get(_API_KEY_VAR) == "real-key"


# ---------------------------------------------------------------------------
# configure_credentials() — no credentials → sys.exit
# ---------------------------------------------------------------------------


class TestConfigureCredentialsNoCredentials:
    def test_exits_when_no_credentials(self, monkeypatch):
        _remove_auth_vars()
        with pytest.raises(SystemExit):
            configure_credentials()

    def test_exit_message_mentions_api_key(self, monkeypatch, capsys):
        _remove_auth_vars()
        with pytest.raises(SystemExit) as exc_info:
            configure_credentials()
        assert "ANTHROPIC_API_KEY" in str(exc_info.value)

    def test_exit_message_mentions_oauth(self, monkeypatch, capsys):
        _remove_auth_vars()
        with pytest.raises(SystemExit) as exc_info:
            configure_credentials()
        assert "CLAUDE_CODE_OAUTH_TOKEN" in str(exc_info.value)


# ---------------------------------------------------------------------------
# is_cloud_provider() helper
# ---------------------------------------------------------------------------


class TestIsCloudProvider:
    def test_false_when_no_cloud_var(self, monkeypatch):
        _remove_auth_vars()
        assert is_cloud_provider() is False

    def test_true_for_bedrock(self, monkeypatch):
        _remove_auth_vars()
        monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
        assert is_cloud_provider() is True

    def test_true_for_vertex(self, monkeypatch):
        _remove_auth_vars()
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "yes")
        assert is_cloud_provider() is True


# ---------------------------------------------------------------------------
# effective_api_key() helper
# ---------------------------------------------------------------------------


class TestEffectiveApiKey:
    def test_returns_none_when_unset(self, monkeypatch):
        _remove_auth_vars()
        assert effective_api_key() is None

    def test_returns_key_when_set(self, monkeypatch):
        _remove_auth_vars()
        monkeypatch.setenv(_API_KEY_VAR, "sk-test")
        assert effective_api_key() == "sk-test"
