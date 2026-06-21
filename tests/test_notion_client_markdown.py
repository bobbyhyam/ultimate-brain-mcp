"""Request-shape + API-version tests for the markdown client methods.

No Notion credentials needed: an httpx.MockTransport captures each outgoing
request so we can assert the URL, method, body, and Notion-Version header — and
crucially that the per-call 2026-03-11 override does not leak into other calls.
"""

from __future__ import annotations

import json

import httpx
import pytest

from ultimate_brain_mcp import notion_client as nc
from ultimate_brain_mcp.notion_client import (
    MARKDOWN_NOTION_VERSION,
    NOTION_VERSION,
    NotionClient,
)


def _client_with_recorder():
    """Return (client, requests_list) where requests_list captures each call."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        # Minimal valid bodies for the methods under test.
        if request.url.path.endswith("/markdown"):
            return httpx.Response(200, json={"object": "page_markdown", "markdown": "x"})
        return httpx.Response(200, json={"object": "page", "id": "p"})

    # Disable rate-limit sleeping; inject the mock transport into the client.
    client = NotionClient("secret-token", rate_per_sec=10_000)
    client._client = httpx.AsyncClient(
        base_url=nc.NOTION_BASE,
        headers={
            "Authorization": "Bearer secret-token",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        transport=httpx.MockTransport(handler),
    )
    return client, captured


@pytest.mark.asyncio
async def test_get_page_markdown_request_shape():
    client, reqs = _client_with_recorder()
    try:
        await client.get_page_markdown("PID")
    finally:
        await client.close()
    req = reqs[-1]
    assert req.method == "GET"
    assert req.url.path == "/v1/pages/PID/markdown"
    assert req.headers["Notion-Version"] == MARKDOWN_NOTION_VERSION


@pytest.mark.asyncio
async def test_replace_page_markdown_request_shape():
    client, reqs = _client_with_recorder()
    try:
        await client.replace_page_markdown("PID", "# Body", allow_deleting_content=True)
    finally:
        await client.close()
    req = reqs[-1]
    assert req.method == "PATCH"
    assert req.url.path == "/v1/pages/PID/markdown"
    assert req.headers["Notion-Version"] == MARKDOWN_NOTION_VERSION
    body = json.loads(req.content)
    assert body["type"] == "replace_content"
    assert body["replace_content"]["new_str"] == "# Body"
    assert body["replace_content"]["allow_deleting_content"] is True


@pytest.mark.asyncio
async def test_update_page_markdown_request_shape():
    client, reqs = _client_with_recorder()
    edits = [{"old_str": "a", "new_str": "b", "replace_all_matches": True}]
    try:
        await client.update_page_markdown("PID", edits)
    finally:
        await client.close()
    req = reqs[-1]
    assert req.method == "PATCH"
    assert req.url.path == "/v1/pages/PID/markdown"
    assert req.headers["Notion-Version"] == MARKDOWN_NOTION_VERSION
    body = json.loads(req.content)
    assert body["type"] == "update_content"
    assert body["update_content"]["content_updates"] == edits


@pytest.mark.asyncio
async def test_notion_version_override_does_not_leak():
    """A markdown call uses 2026-03-11; surrounding normal calls stay on the
    stable default version (no per-client header mutation)."""
    client, reqs = _client_with_recorder()
    try:
        await client.get_page("PID")            # normal
        await client.get_page_markdown("PID")   # override
        await client.get_page("PID")            # normal again
    finally:
        await client.close()
    versions = [r.headers["Notion-Version"] for r in reqs]
    assert versions == [NOTION_VERSION, MARKDOWN_NOTION_VERSION, NOTION_VERSION]
