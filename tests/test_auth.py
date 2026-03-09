"""Tests for OIDC token verification (no credentials needed)."""

import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("jwt", reason="pyjwt[crypto] not installed")

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization


@pytest.fixture
def rsa_keypair():
    """Generate an RSA key pair for testing."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    return private_key, public_key


@pytest.fixture
def jwk_dict(rsa_keypair):
    """Return the public key as a JWK dict."""
    _, public_key = rsa_keypair
    from jwt.algorithms import RSAAlgorithm

    jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk["kid"] = "test-key-1"
    jwk["use"] = "sig"
    return jwk


@pytest.fixture
def make_token(rsa_keypair):
    """Factory to create signed JWTs."""

    def _make(
        issuer="https://auth.example.com",
        audience="my-api",
        scope="read write",
        exp_offset=3600,
        extra_claims=None,
    ):
        private_key, _ = rsa_keypair
        now = int(time.time())
        claims = {
            "iss": issuer,
            "aud": audience,
            "exp": now + exp_offset,
            "iat": now,
            "sub": "user-123",
            "client_id": "test-client",
        }
        if scope is not None:
            claims["scope"] = scope
        if extra_claims:
            claims.update(extra_claims)
        return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key-1"})

    return _make


@pytest.fixture
def verifier(jwk_dict):
    """Create an OIDCTokenVerifier with mocked JWKS."""
    from ultimate_brain_mcp.auth import OIDCTokenVerifier

    v = OIDCTokenVerifier(
        issuer_url="https://auth.example.com",
        audience="my-api",
    )

    # Mock the JWKS client to return our test key
    from jwt import PyJWKClient

    mock_jwks = MagicMock(spec=PyJWKClient)
    mock_signing_key = MagicMock()

    from jwt.algorithms import RSAAlgorithm
    from cryptography.hazmat.primitives.asymmetric import rsa as rsa_mod

    # We need to make get_signing_key_from_jwt return the actual public key
    # so JWT verification works
    v._jwks_client = mock_jwks

    return v


def _patch_jwks(verifier, rsa_keypair):
    """Patch verifier's JWKS client to use test keys."""
    _, public_key = rsa_keypair
    mock_key = MagicMock()
    mock_key.key = public_key
    verifier._jwks_client.get_signing_key_from_jwt.return_value = mock_key


def test_verify_valid_token(verifier, make_token, rsa_keypair):
    _patch_jwks(verifier, rsa_keypair)
    token = make_token()
    result = asyncio.run(verifier.verify_token(token))
    assert result is not None
    assert result.client_id == "test-client"
    assert "read" in result.scopes
    assert "write" in result.scopes


def test_reject_expired_token(verifier, make_token, rsa_keypair):
    _patch_jwks(verifier, rsa_keypair)
    token = make_token(exp_offset=-100)
    result = asyncio.run(verifier.verify_token(token))
    assert result is None


def test_reject_wrong_audience(verifier, make_token, rsa_keypair):
    _patch_jwks(verifier, rsa_keypair)
    token = make_token(audience="wrong-api")
    result = asyncio.run(verifier.verify_token(token))
    assert result is None


def test_reject_wrong_issuer(verifier, make_token, rsa_keypair):
    _patch_jwks(verifier, rsa_keypair)
    token = make_token(issuer="https://evil.example.com")
    result = asyncio.run(verifier.verify_token(token))
    assert result is None


def test_extract_scopes(verifier, make_token, rsa_keypair):
    _patch_jwks(verifier, rsa_keypair)
    token = make_token(scope="admin read write")
    result = asyncio.run(verifier.verify_token(token))
    assert result is not None
    assert result.scopes == ["admin", "read", "write"]


def test_empty_scope(verifier, make_token, rsa_keypair):
    _patch_jwks(verifier, rsa_keypair)
    token = make_token(scope=None)
    result = asyncio.run(verifier.verify_token(token))
    assert result is not None
    assert result.scopes == []


def test_required_scopes_pass(make_token, rsa_keypair, jwk_dict):
    from ultimate_brain_mcp.auth import OIDCTokenVerifier

    v = OIDCTokenVerifier(
        issuer_url="https://auth.example.com",
        audience="my-api",
        required_scopes=["read"],
    )
    v._jwks_client = MagicMock()
    _patch_jwks(v, rsa_keypair)

    token = make_token(scope="read write")
    result = asyncio.run(v.verify_token(token))
    assert result is not None


def test_required_scopes_fail(make_token, rsa_keypair, jwk_dict):
    from ultimate_brain_mcp.auth import OIDCTokenVerifier

    v = OIDCTokenVerifier(
        issuer_url="https://auth.example.com",
        audience="my-api",
        required_scopes=["admin"],
    )
    v._jwks_client = MagicMock()
    _patch_jwks(v, rsa_keypair)

    token = make_token(scope="read write")
    result = asyncio.run(v.verify_token(token))
    assert result is None


def test_create_token_verifier_from_env():
    from ultimate_brain_mcp.auth import create_token_verifier

    with patch.dict("os.environ", {
        "OAUTH_ISSUER_URL": "https://auth.example.com",
        "OAUTH_CLIENT_ID": "my-api",
        "OAUTH_REQUIRED_SCOPES": "read,write",
    }):
        v = create_token_verifier()
        assert v.issuer_url == "https://auth.example.com"
        assert v.audience == "my-api"
        assert v.required_scopes == ["read", "write"]


def test_create_auth_settings_from_env():
    from ultimate_brain_mcp.auth import create_auth_settings

    with patch.dict("os.environ", {
        "OAUTH_ISSUER_URL": "https://auth.example.com",
        "MCP_BASE_URL": "http://mcp.example.com",
    }):
        settings = create_auth_settings()
        assert str(settings.issuer_url) == "https://auth.example.com/"
        assert str(settings.resource_server_url) == "http://mcp.example.com/"
