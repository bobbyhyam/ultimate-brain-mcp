"""Live API tool tests via MCP in-memory client."""

from __future__ import annotations

import json
import os

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


# ---------------------------------------------------------------------------
# Helper: call a tool via in-process MCP session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def server_params():
    """StdioServerParameters for launching the server as a subprocess."""
    env = {**os.environ}
    return StdioServerParameters(
        command="uv",
        args=["run", "ultimate-brain-mcp"],
        env=env,
    )


@pytest.fixture(scope="session")
def _check_env():
    """Skip all tests if required env vars aren't set."""
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


def _parse_result(result):
    """Parse a CallToolResult into Python objects.

    FastMCP serializes list results as multiple TextContent items (one per element)
    and dict results as a single TextContent item. Empty lists produce 0 content items.
    """
    texts = [c.text for c in result.content if hasattr(c, "text")]
    if not texts:
        return []
    # Try parsing the first item — if it's a complete JSON object/array, return it directly
    # (this handles dicts and single-element responses like daily_summary)
    if len(texts) == 1:
        return json.loads(texts[0])
    # Multiple text items — each is a separate JSON object (list serialization)
    return [json.loads(t) for t in texts]


# ---------------------------------------------------------------------------
# Tool tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools(server_params, _check_env):
    """Verify all 28 tools are registered."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            assert len(names) >= 28, f"Expected 28+ tools, got {len(names)}: {names}"

            expected = [
                "search_tasks", "get_my_day", "get_inbox_tasks",
                "create_task", "update_task", "complete_task",
                "search_projects", "get_project_detail",
                "create_project", "update_project",
                "search_notes", "get_note_content",
                "create_note", "update_note",
                "search_tags", "create_tag", "update_tag",
                "search_goals", "get_goal_detail",
                "create_goal", "update_goal",
                "daily_summary", "archive_item", "set_page_content",
                "query_database", "get_page", "get_page_content", "update_page",
            ]
            for name in expected:
                assert name in names, f"Tool '{name}' not found"


@pytest.mark.asyncio
async def test_search_tasks(server_params, _check_env):
    """search_tasks returns tasks (list of dicts or empty list)."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("search_tasks", {"limit": 5})
            data = _parse_result(result)
            assert isinstance(data, list)
            if data:
                assert "id" in data[0]
                assert "name" in data[0]
                assert "status" in data[0]


@pytest.mark.asyncio
async def test_search_tags(server_params, _check_env):
    """search_tags returns tags (may be empty on fresh UB instance)."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("search_tags", {"limit": 5})
            data = _parse_result(result)
            assert isinstance(data, list)
            if data:
                assert "id" in data[0]
                assert "name" in data[0]


@pytest.mark.asyncio
async def test_search_notes(server_params, _check_env):
    """search_notes returns notes (may be empty on fresh UB instance)."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("search_notes", {"limit": 5})
            data = _parse_result(result)
            assert isinstance(data, list)
            if data:
                assert "id" in data[0]
                assert "name" in data[0]
                assert "type" in data[0]


@pytest.mark.asyncio
async def test_search_notes_query(server_params, _check_env):
    """search_notes with query filters by title text."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            # A nonsense query should return no matches, proving the filter is active
            result = await session.call_tool(
                "search_notes", {"query": "zzz_nonexistent_xyzzy_99", "limit": 5}
            )
            data = _parse_result(result)
            assert isinstance(data, list)
            assert len(data) == 0


@pytest.mark.asyncio
async def test_search_projects(server_params, _check_env):
    """search_projects returns projects (may be empty on fresh UB instance)."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("search_projects", {"limit": 5})
            data = _parse_result(result)
            assert isinstance(data, list)


@pytest.mark.asyncio
async def test_search_goals(server_params, _check_env):
    """search_goals returns goals (may be empty on fresh UB instance)."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("search_goals", {"limit": 5})
            data = _parse_result(result)
            assert isinstance(data, list)


@pytest.mark.asyncio
async def test_daily_summary(server_params, _check_env):
    """daily_summary returns a structured summary dict."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("daily_summary", {})
            data = _parse_result(result)
            assert isinstance(data, dict)
            assert "date" in data
            assert "my_day" in data
            assert "overdue" in data
            assert "inbox_count" in data
            assert "active_projects_count" in data
            assert "active_goals_count" in data


@pytest.mark.asyncio
async def test_query_database_list(server_params, _check_env):
    """query_database without args lists available databases."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("query_database", {})
            data = _parse_result(result)
            assert isinstance(data, dict)
            assert "available_databases" in data or "error" in data
