"""GitHub Actions OIDC verification for the private market-data relay."""

from __future__ import annotations

import os
from typing import Any

import jwt
from jwt import PyJWKClient

ISSUER = "https://token.actions.githubusercontent.com"
AUDIENCE = "wude-live-data-relay"
JWKS_URL = f"{ISSUER}/.well-known/jwks"
DEFAULT_REPOSITORY = "a0953883367/wude-ai-stock-v6"
DEFAULT_WORKFLOW = ".github/workflows/stock-briefing.yml"

_JWK_CLIENT = PyJWKClient(JWKS_URL, cache_keys=True)


def validate_claims(claims: dict[str, Any]) -> dict[str, Any]:
    repository = os.getenv("GITHUB_OIDC_ALLOWED_REPOSITORY", DEFAULT_REPOSITORY).strip()
    workflow = os.getenv("GITHUB_OIDC_ALLOWED_WORKFLOW", DEFAULT_WORKFLOW).strip()
    if claims.get("repository") != repository:
        raise PermissionError("GitHub repository is not allowed")
    if claims.get("ref") != "refs/heads/main":
        raise PermissionError("GitHub ref is not allowed")
    workflow_ref = str(claims.get("workflow_ref") or "")
    if workflow_ref != f"{repository}/{workflow}@refs/heads/main":
        raise PermissionError("GitHub workflow is not allowed")
    if claims.get("event_name") not in {"schedule", "workflow_dispatch"}:
        raise PermissionError("GitHub event is not allowed")
    return claims


def verify_token(token: str) -> dict[str, Any]:
    if not token:
        raise PermissionError("GitHub OIDC token is missing")
    signing_key = _JWK_CLIENT.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=AUDIENCE,
        issuer=ISSUER,
    )
    return validate_claims(claims)
