"""SSO authentication for the remote MCP server.

Supports two modes:

1. **Token verifier mode** (legacy) — MCP server acts as an OAuth Resource
   Server only. Clients must obtain tokens from the OIDC provider directly.
   Activated when ``OAUTH_ISSUER_URL`` is set but ``OAUTH_CLIENT_SECRET`` is not.

2. **Authorization server mode** — MCP server acts as a full OAuth
   Authorization Server that proxies authentication to an upstream OIDC
   provider (e.g. Authentik). Claude.ai and other MCP clients that expect
   RFC 8414 discovery work out of the box.
   Activated when both ``OAUTH_ISSUER_URL`` and ``OAUTH_CLIENT_SECRET`` are set.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthToken,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

logger = logging.getLogger(__name__)


class _PermissiveClient(OAuthClientInformationFull):
    """Client that accepts any redirect URI and scope.

    Used for auto-created clients when a client_id isn't found in the
    in-memory store (e.g. after a container restart). This is safe because
    real authentication is handled by the upstream IdP (Authentik), not by
    client credentials.
    """

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        if redirect_uri is not None:
            return redirect_uri
        if self.redirect_uris and len(self.redirect_uris) == 1:
            return self.redirect_uris[0]
        from mcp.shared.auth import InvalidRedirectUriError
        raise InvalidRedirectUriError("redirect_uri is required")

    def validate_scope(self, requested_scope: str | None) -> list[str] | None:
        if requested_scope is None:
            return None
        return requested_scope.split(" ")

# ---------------------------------------------------------------------------
# OIDC Token Verifier (shared by both modes)
# ---------------------------------------------------------------------------


class OIDCTokenVerifier:
    """Validates JWTs issued by the SSO provider after browser login.

    Implements the ``TokenVerifier`` protocol expected by FastMCP.
    Called automatically on every authenticated request — users never
    interact with tokens directly.
    """

    def __init__(
        self,
        issuer_url: str,
        audience: str,
        *,
        required_scopes: list[str] | None = None,
        algorithms: list[str] | None = None,
    ) -> None:
        # Preserve the issuer URL exactly as configured — trailing slash matters
        # because the JWT's iss claim must match exactly.
        self.issuer_url = issuer_url
        self.audience = audience
        self.required_scopes = required_scopes or []
        self.algorithms = algorithms or ["RS256", "ES256"]
        self._jwks_client: PyJWKClient | None = None

    async def _discover_jwks_uri(self) -> str:
        """Fetch the JWKS URI from the OIDC discovery document."""
        base = self.issuer_url.rstrip("/")
        discovery_url = f"{base}/.well-known/openid-configuration"
        async with httpx.AsyncClient() as client:
            resp = await client.get(discovery_url)
            resp.raise_for_status()
            return resp.json()["jwks_uri"]

    async def _get_jwks_client(self) -> PyJWKClient:
        if self._jwks_client is None:
            jwks_uri = await self._discover_jwks_uri()
            logger.info("JWKS URI discovered: %s", jwks_uri)
            self._jwks_client = PyJWKClient(jwks_uri, cache_keys=True)
        return self._jwks_client

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a bearer token and return access info if valid."""
        try:
            jwks_client = await self._get_jwks_client()
            signing_key = jwks_client.get_signing_key_from_jwt(token)

            # Try exact issuer match first, then with/without trailing slash
            issuer_variants = [self.issuer_url]
            if self.issuer_url.endswith("/"):
                issuer_variants.append(self.issuer_url.rstrip("/"))
            else:
                issuer_variants.append(self.issuer_url + "/")

            payload = None
            last_error = None
            for issuer in issuer_variants:
                try:
                    payload = jwt.decode(
                        token,
                        signing_key.key,
                        algorithms=self.algorithms,
                        audience=self.audience,
                        issuer=issuer,
                        options={
                            "require": ["exp", "iss"],
                            "verify_aud": bool(self.audience),
                        },
                    )
                    break
                except jwt.InvalidIssuerError as e:
                    last_error = e
                    continue

            if payload is None:
                logger.warning("Token issuer mismatch. Expected: %s", issuer_variants)
                if last_error:
                    logger.warning("Last error: %s", last_error)
                return None

            scopes = payload.get("scope", "").split() if payload.get("scope") else []

            for required in self.required_scopes:
                if required not in scopes:
                    logger.warning("Missing required scope: %s (has: %s)", required, scopes)
                    return None

            return AccessToken(
                token=token,
                client_id=payload.get("client_id", payload.get("azp", "")),
                scopes=scopes,
                expires_at=payload.get("exp"),
            )
        except jwt.PyJWTError as e:
            logger.warning("JWT verification failed: %s", e)
            return None
        except (httpx.HTTPError, KeyError) as e:
            logger.warning("Token verification error: %s", e)
            return None


# ---------------------------------------------------------------------------
# OAuth Authorization Server Provider
# ---------------------------------------------------------------------------

PENDING_AUTH_TTL = 600  # 10 minutes


@dataclass
class PendingAuth:
    """State stored between authorize() and the OAuth callback."""

    client_id: str
    redirect_uri: str
    state: str | None
    code_challenge: str
    scopes: list[str]
    redirect_uri_provided_explicitly: bool
    authentik_state: str
    resource: str | None = None
    created_at: float = field(default_factory=time.time)


class AuthentikOAuthProvider:
    """OAuth Authorization Server that delegates to an upstream OIDC provider.

    Implements the ``OAuthAuthorizationServerProvider`` protocol so the MCP
    server advertises ``/.well-known/oauth-authorization-server`` and handles
    ``/authorize``, ``/token``, and ``/register`` endpoints itself.

    Authentication is proxied to the upstream provider (e.g. Authentik):
    the MCP server is a confidential OAuth client of the upstream IdP, and
    passes through the IdP's JWTs as access tokens to MCP clients.
    """

    def __init__(
        self,
        issuer_url: str,
        client_id: str,
        client_secret: str,
        mcp_base_url: str,
        token_verifier: OIDCTokenVerifier,
        *,
        required_scopes: list[str] | None = None,
    ) -> None:
        self.issuer_url = issuer_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.mcp_base_url = mcp_base_url.rstrip("/")
        self._token_verifier = token_verifier
        self.required_scopes = required_scopes or []

        # In-memory stores (acceptable for single-instance personal server)
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._pending_auth: dict[str, PendingAuth] = {}  # keyed by authentik_state
        self._auth_codes: dict[str, AuthorizationCode] = {}  # keyed by code
        self._code_token_map: dict[str, dict] = {}  # code -> {access_token, refresh_token, expires_in}
        self._refresh_tokens: dict[str, dict] = {}  # refresh_token -> {upstream_refresh, client_id, scopes}

        # Lazily discovered OIDC endpoints
        self._oidc_config: dict | None = None

    async def _discover_oidc_config(self) -> dict:
        """Fetch and cache the upstream OIDC discovery document."""
        if self._oidc_config is None:
            base = self.issuer_url.rstrip("/")
            url = f"{base}/.well-known/openid-configuration"
            async with httpx.AsyncClient() as client:
                resp = await client.get(url)
                resp.raise_for_status()
                self._oidc_config = resp.json()
        return self._oidc_config

    def _prune_pending(self) -> None:
        """Remove expired pending auth entries."""
        now = time.time()
        expired = [k for k, v in self._pending_auth.items() if now - v.created_at > PENDING_AUTH_TTL]
        for k in expired:
            del self._pending_auth[k]

    # -- Client registration --------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        client = self._clients.get(client_id)
        if client is not None:
            return client
        # Auto-accept unknown clients (e.g. after container restart loses
        # in-memory registrations). Real auth is handled by the upstream IdP.
        logger.info("Auto-accepting unknown client_id: %s", client_id)
        return _PermissiveClient(
            client_id=client_id,
            redirect_uris=["https://placeholder.invalid/callback"],
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info

    # -- Authorization ---------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Redirect the user to the upstream IdP for authentication."""
        self._prune_pending()

        authentik_state = secrets.token_urlsafe(32)
        pending = PendingAuth(
            client_id=client.client_id,
            redirect_uri=str(params.redirect_uri),
            state=params.state,
            code_challenge=params.code_challenge,
            scopes=params.scopes or [],
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=getattr(params, "resource", None),
            authentik_state=authentik_state,
        )
        self._pending_auth[authentik_state] = pending

        oidc = await self._discover_oidc_config()
        authorize_url = oidc["authorization_endpoint"]

        query = urlencode({
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": f"{self.mcp_base_url}/oauth/callback",
            "state": authentik_state,
            "scope": "openid profile email",
        })
        return f"{authorize_url}?{query}"

    # -- OAuth callback (custom route) ----------------------------------------

    async def handle_oauth_callback(self, code: str, state: str) -> str:
        """Exchange upstream auth code for tokens and redirect back to MCP client.

        Returns the redirect URL to send the user back to the MCP client.
        """
        pending = self._pending_auth.pop(state, None)
        if pending is None:
            raise ValueError("Invalid or expired OAuth state")

        # Exchange code at upstream token endpoint
        oidc = await self._discover_oidc_config()
        token_url = oidc["token_endpoint"]

        async with httpx.AsyncClient() as http:
            resp = await http.post(
                token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": f"{self.mcp_base_url}/oauth/callback",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            resp.raise_for_status()
            upstream_tokens = resp.json()

        # Generate MCP authorization code
        mcp_code = secrets.token_urlsafe(32)
        expires_at = time.time() + 300  # 5 minutes

        self._auth_codes[mcp_code] = AuthorizationCode(
            code=mcp_code,
            scopes=pending.scopes,
            expires_at=expires_at,
            client_id=pending.client_id,
            code_challenge=pending.code_challenge,
            redirect_uri=pending.redirect_uri,
            redirect_uri_provided_explicitly=pending.redirect_uri_provided_explicitly,
            resource=pending.resource,
        )

        self._code_token_map[mcp_code] = {
            "access_token": upstream_tokens["access_token"],
            "refresh_token": upstream_tokens.get("refresh_token"),
            "expires_in": upstream_tokens.get("expires_in"),
        }

        return construct_redirect_uri(
            pending.redirect_uri,
            code=mcp_code,
            state=pending.state,
        )

    # -- Token exchange --------------------------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self._auth_codes.get(authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        tokens = self._code_token_map.pop(authorization_code.code, None)
        del self._auth_codes[authorization_code.code]

        if tokens is None:
            from mcp.server.auth.provider import TokenError
            raise TokenError("invalid_grant", "Authorization code already used or expired")

        # Store refresh token mapping for later refresh
        mcp_refresh = None
        if tokens.get("refresh_token"):
            mcp_refresh = secrets.token_urlsafe(32)
            self._refresh_tokens[mcp_refresh] = {
                "upstream_refresh": tokens["refresh_token"],
                "client_id": client.client_id,
                "scopes": list(authorization_code.scopes),
            }

        return OAuthToken(
            access_token=tokens["access_token"],
            token_type="bearer",
            expires_in=tokens.get("expires_in"),
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
            refresh_token=mcp_refresh,
        )

    # -- Access token verification ---------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        return await self._token_verifier.verify_token(token)

    # -- Refresh token ---------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        entry = self._refresh_tokens.get(refresh_token)
        if entry is None or entry["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=entry["client_id"],
            scopes=entry["scopes"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        entry = self._refresh_tokens.pop(refresh_token.token, None)
        if entry is None:
            from mcp.server.auth.provider import TokenError
            raise TokenError("invalid_grant", "Refresh token not found")

        # Proxy refresh to upstream
        oidc = await self._discover_oidc_config()
        token_url = oidc["token_endpoint"]

        async with httpx.AsyncClient() as http:
            resp = await http.post(
                token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": entry["upstream_refresh"],
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            resp.raise_for_status()
            upstream_tokens = resp.json()

        # Store new refresh token mapping
        mcp_refresh = None
        if upstream_tokens.get("refresh_token"):
            mcp_refresh = secrets.token_urlsafe(32)
            self._refresh_tokens[mcp_refresh] = {
                "upstream_refresh": upstream_tokens["refresh_token"],
                "client_id": client.client_id,
                "scopes": scopes or list(refresh_token.scopes),
            }

        return OAuthToken(
            access_token=upstream_tokens["access_token"],
            token_type="bearer",
            expires_in=upstream_tokens.get("expires_in"),
            scope=" ".join(scopes) if scopes else " ".join(refresh_token.scopes),
            refresh_token=mcp_refresh,
        )

    # -- Revocation ------------------------------------------------------------

    async def revoke_token(
        self, token: AccessToken | RefreshToken
    ) -> None:
        if isinstance(token, RefreshToken):
            self._refresh_tokens.pop(token.token, None)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def create_token_verifier() -> OIDCTokenVerifier:
    """Create an OIDCTokenVerifier from environment variables."""
    issuer_url = os.environ["OAUTH_ISSUER_URL"]
    audience = os.environ["OAUTH_CLIENT_ID"]
    scopes_raw = os.environ.get("OAUTH_REQUIRED_SCOPES", "")
    required_scopes = [s.strip() for s in scopes_raw.split(",") if s.strip()] if scopes_raw else []

    return OIDCTokenVerifier(
        issuer_url=issuer_url,
        audience=audience,
        required_scopes=required_scopes,
    )


def create_auth_settings() -> AuthSettings:
    """Create AuthSettings advertised to MCP clients.

    The issuer_url tells clients where to redirect users for SSO login.
    """
    issuer_url = os.environ["OAUTH_ISSUER_URL"]
    resource_server_url = os.environ.get("MCP_BASE_URL", f"http://localhost:{os.environ.get('MCP_PORT', '8000')}")
    scopes_raw = os.environ.get("OAUTH_REQUIRED_SCOPES", "")
    required_scopes = [s.strip() for s in scopes_raw.split(",") if s.strip()] if scopes_raw else None

    return AuthSettings(
        issuer_url=issuer_url,
        resource_server_url=resource_server_url,
        required_scopes=required_scopes,
    )


def create_oauth_provider() -> tuple[AuthentikOAuthProvider, AuthSettings]:
    """Create an AuthentikOAuthProvider and AuthSettings from environment variables.

    Used when OAUTH_CLIENT_SECRET is set, enabling full OAuth AS mode.
    """
    issuer_url = os.environ["OAUTH_ISSUER_URL"]
    client_id = os.environ["OAUTH_CLIENT_ID"]
    client_secret = os.environ["OAUTH_CLIENT_SECRET"]
    mcp_base_url = os.environ.get("MCP_BASE_URL", f"http://localhost:{os.environ.get('MCP_PORT', '8000')}")
    scopes_raw = os.environ.get("OAUTH_REQUIRED_SCOPES", "")
    required_scopes = [s.strip() for s in scopes_raw.split(",") if s.strip()] if scopes_raw else []

    token_verifier = OIDCTokenVerifier(
        issuer_url=issuer_url,
        audience=client_id,
        required_scopes=required_scopes,
    )

    provider = AuthentikOAuthProvider(
        issuer_url=issuer_url,
        client_id=client_id,
        client_secret=client_secret,
        mcp_base_url=mcp_base_url,
        token_verifier=token_verifier,
        required_scopes=required_scopes,
    )

    # In AS mode, the MCP server itself is the issuer
    auth_settings = AuthSettings(
        issuer_url=mcp_base_url,
        resource_server_url=mcp_base_url,
        client_registration_options=ClientRegistrationOptions(enabled=True),
        required_scopes=required_scopes or None,
    )

    return provider, auth_settings
