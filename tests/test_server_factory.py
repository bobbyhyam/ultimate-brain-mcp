"""Tests for the server factory function (no credentials needed)."""

import asyncio

import pytest


def test_create_mcp_returns_fastmcp():
    from mcp.server.fastmcp import FastMCP

    from ultimate_brain_mcp.server import create_mcp

    server = create_mcp()
    assert isinstance(server, FastMCP)


def test_create_mcp_registers_all_tools():
    from ultimate_brain_mcp.server import create_mcp

    server = create_mcp()
    tools = asyncio.run(server.list_tools())
    assert len(tools) == 28


def test_create_mcp_no_auth_by_default():
    from ultimate_brain_mcp.server import create_mcp

    server = create_mcp()
    assert server._token_verifier is None


def test_create_mcp_with_auth():
    from unittest.mock import MagicMock

    from mcp.server.auth.settings import AuthSettings

    from ultimate_brain_mcp.server import create_mcp

    mock_verifier = MagicMock()
    auth = AuthSettings(
        issuer_url="https://auth.example.com",
        resource_server_url="http://localhost:8000",
    )

    server = create_mcp(auth=auth, token_verifier=mock_verifier)
    assert server._token_verifier is mock_verifier


def test_create_mcp_with_auth_server_provider():
    from unittest.mock import AsyncMock, MagicMock

    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

    from ultimate_brain_mcp.server import create_mcp

    mock_provider = MagicMock()
    mock_provider.handle_oauth_callback = AsyncMock()
    auth = AuthSettings(
        issuer_url="https://mcp.example.com",
        resource_server_url="https://mcp.example.com",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )

    server = create_mcp(auth=auth, auth_server_provider=mock_provider)
    # Provider wraps itself as token verifier via ProviderTokenVerifier
    assert server._auth_server_provider is mock_provider
    assert server._token_verifier is not None

    tools = asyncio.run(server.list_tools())
    assert len(tools) == 28

    # Custom route should be registered
    assert any(r.path == "/oauth/callback" for r in server._custom_starlette_routes)


def test_default_mcp_instance_exists():
    from mcp.server.fastmcp import FastMCP

    from ultimate_brain_mcp.server import mcp

    assert isinstance(mcp, FastMCP)
    tools = asyncio.run(mcp.list_tools())
    assert len(tools) == 28
