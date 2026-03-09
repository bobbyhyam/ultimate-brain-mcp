"""Integration tests for the remote (HTTP) MCP server.

Starts the server as a subprocess with MCP_TRANSPORT=streamable-http,
connects via streamable_http_client, and verifies tools work end-to-end.
Requires Notion credentials in env (skips gracefully if missing).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

PORT = 18741  # Use a non-standard port to avoid conflicts


@pytest.fixture(scope="module")
def _check_env():
    required = [
        "NOTION_INTEGRATION_SECRET",
        "UB_TASKS_DS_ID",
        "UB_PROJECTS_DS_ID",
        "UB_NOTES_DS_ID",
        "UB_TAGS_DS_ID",
        "UB_GOALS_DS_ID",
    ]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        pytest.skip(f"Missing env vars: {', '.join(missing)}")


@pytest.fixture(scope="module")
def remote_server(_check_env):
    """Start the MCP server in HTTP mode as a subprocess, wait for ready."""
    env = {
        **os.environ,
        "MCP_TRANSPORT": "streamable-http",
        "MCP_HOST": "127.0.0.1",
        "MCP_PORT": str(PORT),
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", "from ultimate_brain_mcp import main; main()"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to be ready (up to 15 seconds)
    url = f"http://127.0.0.1:{PORT}/mcp"
    deadline = time.monotonic() + 15
    ready = False
    while time.monotonic() < deadline:
        try:
            resp = httpx.post(
                url,
                json={
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0.1"},
                    },
                },
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                timeout=2,
            )
            if resp.status_code == 200:
                ready = True
                break
        except (httpx.ConnectError, httpx.ReadTimeout):
            pass
        time.sleep(0.5)

    if not ready:
        proc.terminate()
        proc.wait(timeout=5)
        stderr = proc.stderr.read().decode() if proc.stderr else ""
        pytest.fail(f"Remote MCP server failed to start within 15s. stderr:\n{stderr}")

    yield url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _parse_result(result):
    """Parse a CallToolResult into Python objects."""
    texts = [c.text for c in result.content if hasattr(c, "text")]
    if not texts:
        return []
    if len(texts) == 1:
        return json.loads(texts[0])
    return [json.loads(t) for t in texts]


@pytest.mark.asyncio
async def test_remote_list_tools(remote_server):
    """Verify all 28 tools are reachable via HTTP transport."""
    async with streamable_http_client(remote_server) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            assert len(names) >= 28, f"Expected 28+ tools, got {len(names)}: {names}"


@pytest.mark.asyncio
async def test_remote_search_tasks(remote_server):
    """search_tasks returns results via HTTP transport."""
    async with streamable_http_client(remote_server) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("search_tasks", {"limit": 3})
            data = _parse_result(result)
            assert isinstance(data, list)
            if data:
                assert "id" in data[0]
                assert "name" in data[0]


@pytest.mark.asyncio
async def test_remote_daily_summary(remote_server):
    """daily_summary returns a structured dict via HTTP transport."""
    async with streamable_http_client(remote_server) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("daily_summary", {})
            data = _parse_result(result)
            assert isinstance(data, dict)
            assert "date" in data
            assert "my_day" in data
