"""
parallax_engine.auth
====================
Credential resolver for the agent harness.

Implements the auth swap described in SPEC.md §3.5:
  - ANTHROPIC_API_KEY  → production path (api-key)
  - CLAUDE_CODE_OAUTH_TOKEN → dev path (oauth), mapped to ANTHROPIC_API_KEY
  - CLAUDE_CODE_USE_BEDROCK / _VERTEX / _FOUNDRY → cloud-provider path

Commercial distribution requires ANTHROPIC_API_KEY per Anthropic SDK terms.
"""

from __future__ import annotations

import os
import sys

# Environment variable names (kept as constants to aid refactoring and tests)
_CLOUD_PROVIDER_VARS: tuple[str, ...] = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
_API_KEY_VAR = "ANTHROPIC_API_KEY"
_OAUTH_TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"


def configure_credentials() -> str:
    """
    Resolve API credentials and ensure ANTHROPIC_API_KEY is set in the
    current process environment before any SDK call is made.

    Returns
    -------
    str
        One of:
          'cloud-provider'  — Bedrock / Vertex AI / Foundry env var is set
          'api-key'         — ANTHROPIC_API_KEY is set
          'oauth'           — CLAUDE_CODE_OAUTH_TOKEN was mapped to ANTHROPIC_API_KEY

    Exits
    -----
    sys.exit(1) if no credentials are found at all.
    """
    # Cloud-provider credentials (Bedrock / Vertex / Foundry) take precedence.
    if any(os.getenv(k) for k in _CLOUD_PROVIDER_VARS):
        return "cloud-provider"

    # Direct API key — simplest production path.
    if os.getenv(_API_KEY_VAR):
        return "api-key"

    # Dev/OAuth path: map the OAuth token to the SDK envvar.
    oauth_token = os.getenv(_OAUTH_TOKEN_VAR)
    if oauth_token:
        os.environ[_API_KEY_VAR] = oauth_token
        return "oauth"

    # Nothing found — abort with an actionable message.
    sys.exit(
        "parallax-engine: no credentials found. "
        "Set ANTHROPIC_API_KEY (production) or CLAUDE_CODE_OAUTH_TOKEN (dev). "
        "For Bedrock/Vertex/Foundry, set the appropriate CLAUDE_CODE_USE_* variable."
    )


def is_cloud_provider() -> bool:
    """Return True if a cloud provider (Bedrock/Vertex/Foundry) is active."""
    return any(os.getenv(k) for k in _CLOUD_PROVIDER_VARS)


def effective_api_key() -> str | None:
    """Return the effective ANTHROPIC_API_KEY value after credentials are resolved."""
    return os.getenv(_API_KEY_VAR)
