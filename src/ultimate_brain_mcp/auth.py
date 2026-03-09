"""SSO authentication for the remote MCP server.

When an MCP client connects to the remote server:
1. The server advertises the OIDC provider via OAuth Protected Resource Metadata.
2. The client opens the provider's login page in the user's browser.
3. After the user logs in, the client receives a JWT access token.
4. The client sends the JWT on every request; this module validates it
   against the provider's public keys (JWKS).

The user experience is: connect to the MCP server URL → browser opens SSO
login → done. No manual token management required.
"""

from __future__ import annotations

import logging
import os

import httpx
import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

logger = logging.getLogger(__name__)


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
