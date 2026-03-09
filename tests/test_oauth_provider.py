"""Tests for AuthentikOAuthProvider (no credentials needed)."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ultimate_brain_mcp.auth import (
    AuthentikOAuthProvider,
    OIDCTokenVerifier,
    create_oauth_provider,
)


@pytest.fixture
def mock_verifier():
    v = MagicMock(spec=OIDCTokenVerifier)
    v.verify_token = AsyncMock(return_value=None)
    return v


@pytest.fixture
def provider(mock_verifier):
    return AuthentikOAuthProvider(
        issuer_url="https://auth.example.com",
        client_id="mcp-client",
        client_secret="mcp-secret",
        mcp_base_url="https://mcp.example.com",
        token_verifier=mock_verifier,
    )


def _fake_oidc_config():
    return {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/token",
        "jwks_uri": "https://auth.example.com/jwks",
    }


def _make_client_info(client_id="test-client-1"):
    from mcp.shared.auth import OAuthClientInformationFull

    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret="secret",
        redirect_uris=["https://claude.ai/callback"],
    )


class TestClientRegistration:
    def test_register_and_get_client(self, provider):
        client = _make_client_info()
        asyncio.run(provider.register_client(client))
        result = asyncio.run(provider.get_client("test-client-1"))
        assert result is not None
        assert result.client_id == "test-client-1"

    def test_get_unknown_client_auto_accepted(self, provider):
        result = asyncio.run(provider.get_client("nonexistent"))
        assert result is not None
        assert result.client_id == "nonexistent"


class TestAuthorize:
    def test_authorize_returns_upstream_url(self, provider):
        from mcp.server.auth.provider import AuthorizationParams

        provider._oidc_config = _fake_oidc_config()
        client = _make_client_info()

        params = AuthorizationParams(
            state="client-state-123",
            scopes=["read"],
            code_challenge="challenge123",
            redirect_uri="https://claude.ai/callback",
            redirect_uri_provided_explicitly=True,
        )

        url = asyncio.run(provider.authorize(client, params))

        assert url.startswith("https://auth.example.com/authorize?")
        assert "response_type=code" in url
        assert "client_id=mcp-client" in url
        assert "redirect_uri=" in url
        assert "oauth%2Fcallback" in url or "oauth/callback" in url
        assert "scope=openid+profile+email" in url or "scope=openid" in url

        # Verify pending auth was stored
        assert len(provider._pending_auth) == 1
        pending = list(provider._pending_auth.values())[0]
        assert pending.client_id == "test-client-1"
        assert pending.redirect_uri == "https://claude.ai/callback"
        assert pending.state == "client-state-123"
        assert pending.code_challenge == "challenge123"

    def test_authorize_prunes_expired(self, provider):
        from mcp.server.auth.provider import AuthorizationParams

        provider._oidc_config = _fake_oidc_config()
        client = _make_client_info()

        # Add an expired entry
        from ultimate_brain_mcp.auth import PendingAuth

        provider._pending_auth["old-state"] = PendingAuth(
            client_id="old",
            redirect_uri="https://old.example.com",
            state="old",
            code_challenge="old",
            scopes=[],
            redirect_uri_provided_explicitly=True,
            authentik_state="old-state",
            created_at=time.time() - 700,
        )

        params = AuthorizationParams(
            state="new",
            scopes=[],
            code_challenge="new-challenge",
            redirect_uri="https://claude.ai/callback",
            redirect_uri_provided_explicitly=True,
        )
        asyncio.run(provider.authorize(client, params))

        assert "old-state" not in provider._pending_auth
        assert len(provider._pending_auth) == 1


class TestCallbackFlow:
    def test_callback_exchanges_code(self, provider):
        provider._oidc_config = _fake_oidc_config()

        # Set up pending auth
        from ultimate_brain_mcp.auth import PendingAuth

        provider._pending_auth["auth-state"] = PendingAuth(
            client_id="test-client-1",
            redirect_uri="https://claude.ai/callback",
            state="client-state-123",
            code_challenge="challenge",
            scopes=["read"],
            redirect_uri_provided_explicitly=True,
            authentik_state="auth-state",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "upstream-jwt-token",
            "refresh_token": "upstream-refresh",
            "expires_in": 3600,
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            redirect_url = asyncio.run(
                provider.handle_oauth_callback("upstream-code", "auth-state")
            )

        assert "code=" in redirect_url
        assert "state=client-state-123" in redirect_url
        assert redirect_url.startswith("https://claude.ai/callback")

        # Verify auth code was stored
        assert len(provider._auth_codes) == 1
        assert len(provider._code_token_map) == 1

    def test_callback_invalid_state(self, provider):
        with pytest.raises(ValueError, match="Invalid or expired"):
            asyncio.run(provider.handle_oauth_callback("code", "bad-state"))


class TestExchangeAuthorizationCode:
    def test_exchange_returns_upstream_token(self, provider):
        from mcp.server.auth.provider import AuthorizationCode

        code = "test-mcp-code"
        provider._auth_codes[code] = AuthorizationCode(
            code=code,
            scopes=["read"],
            expires_at=time.time() + 300,
            client_id="test-client-1",
            code_challenge="challenge",
            redirect_uri="https://claude.ai/callback",
            redirect_uri_provided_explicitly=True,
        )
        provider._code_token_map[code] = {
            "access_token": "upstream-jwt",
            "refresh_token": "upstream-refresh",
            "expires_in": 3600,
        }

        client = _make_client_info()
        auth_code = provider._auth_codes[code]
        token = asyncio.run(provider.exchange_authorization_code(client, auth_code))

        assert token.access_token == "upstream-jwt"
        assert token.expires_in == 3600
        assert token.refresh_token is not None  # MCP-wrapped refresh token
        assert code not in provider._auth_codes
        assert code not in provider._code_token_map


class TestLoadAccessToken:
    def test_delegates_to_verifier(self, provider, mock_verifier):
        from mcp.server.auth.provider import AccessToken

        expected = AccessToken(
            token="jwt-token",
            client_id="test",
            scopes=["read"],
            expires_at=int(time.time()) + 3600,
        )
        mock_verifier.verify_token = AsyncMock(return_value=expected)

        result = asyncio.run(provider.load_access_token("jwt-token"))
        assert result is expected
        mock_verifier.verify_token.assert_called_once_with("jwt-token", verify_exp=False)


class TestRefreshToken:
    def test_refresh_proxied_to_upstream(self, provider):
        from mcp.server.auth.provider import RefreshToken

        provider._oidc_config = _fake_oidc_config()
        provider._refresh_tokens["mcp-refresh"] = {
            "upstream_refresh": "upstream-refresh-token",
            "client_id": "test-client-1",
            "scopes": ["read"],
        }

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new-upstream-jwt",
            "refresh_token": "new-upstream-refresh",
            "expires_in": 3600,
        }
        mock_response.raise_for_status = MagicMock()

        client = _make_client_info()
        rt = RefreshToken(token="mcp-refresh", client_id="test-client-1", scopes=["read"])

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            token = asyncio.run(provider.exchange_refresh_token(client, rt, ["read"]))

        assert token.access_token == "new-upstream-jwt"
        assert token.refresh_token is not None
        # Old refresh token should be consumed
        assert "mcp-refresh" not in provider._refresh_tokens
        # New one should be stored
        assert len(provider._refresh_tokens) == 1

    def test_load_refresh_token_wrong_client(self, provider):
        provider._refresh_tokens["rt"] = {
            "upstream_refresh": "x",
            "client_id": "other-client",
            "scopes": [],
        }
        client = _make_client_info()
        result = asyncio.run(provider.load_refresh_token(client, "rt"))
        assert result is None


class TestCreateOAuthProvider:
    def test_from_env(self):
        env = {
            "OAUTH_ISSUER_URL": "https://auth.example.com",
            "OAUTH_CLIENT_ID": "mcp-client",
            "OAUTH_CLIENT_SECRET": "mcp-secret",
            "MCP_BASE_URL": "https://mcp.example.com",
        }
        with patch.dict("os.environ", env):
            provider, settings = create_oauth_provider()

        assert isinstance(provider, AuthentikOAuthProvider)
        assert provider.client_id == "mcp-client"
        assert provider.client_secret == "mcp-secret"
        assert str(settings.issuer_url) == "https://mcp.example.com/"
        assert settings.client_registration_options is not None
        assert settings.client_registration_options.enabled is True
