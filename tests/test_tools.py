"""Live API tool tests via MCP in-memory client."""

from __future__ import annotations

import json
import os

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

# Whole module exercises the live Notion API — deselected by default (-m 'not live').
pytestmark = pytest.mark.live


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
    """Verify all 30 tools are registered."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            assert len(names) >= 31, f"Expected 31+ tools, got {len(names)}: {names}"

            expected = [
                "search_tasks",
                "get_my_day",
                "get_inbox_tasks",
                "create_task",
                "update_task",
                "complete_task",
                "search_projects",
                "get_project_detail",
                "create_project",
                "update_project",
                "search_notes",
                "get_note_content",
                "create_note",
                "update_note",
                "search_tags",
                "create_tag",
                "update_tag",
                "search_goals",
                "get_goal_detail",
                "create_goal",
                "update_goal",
                "daily_summary",
                "archive_item",
                "set_page_content",
                "patch_page_content",
                "daily_review_snapshot",
                "bulk_update_tasks",
                "query_database",
                "get_page",
                "get_page_content",
                "update_page",
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


@pytest.mark.asyncio
async def test_search_tasks_due_on_conflict(server_params, _check_env):
    """search_tasks(due_on=...) cannot combine with due_before/due_after."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "search_tasks",
                {"due_on": "2026-05-09", "due_before": "2026-05-10"},
            )
            data = _parse_result(result)
            assert isinstance(data, dict)
            assert "error" in data
            assert "due_on" in data["error"]


@pytest.mark.asyncio
async def test_daily_review_snapshot(server_params, _check_env):
    """daily_review_snapshot returns the documented shape in one call."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("daily_review_snapshot", {})
            data = _parse_result(result)
            assert isinstance(data, dict)
            # Time fields
            assert "now" in data and isinstance(data["now"], str)
            assert "timezone" in data and isinstance(data["timezone"], str)
            # Buckets
            assert "buckets" in data
            for key in (
                "completed_today",
                "overdue_or_due_today",
                "due_tomorrow",
                "on_my_day",
                "inbox",
            ):
                assert key in data["buckets"]
                assert isinstance(data["buckets"][key], list)
            # Outstanding union
            assert "outstanding" in data
            assert isinstance(data["outstanding"], list)
            # Lookups
            assert "lookups" in data
            assert "projects" in data["lookups"]
            assert "area_tags" in data["lookups"]
            # Task schema
            assert "task_schema" in data
            for key in (
                "has_location_property",
                "location_property_name",
                "location_property_type",
                "location_options",
                "labels_options",
            ):
                assert key in data["task_schema"]
            # Truncation flags
            assert "truncated" in data
            for key in (
                "completed_today",
                "overdue_or_due_today",
                "due_tomorrow",
                "on_my_day",
                "inbox",
            ):
                assert key in data["truncated"]
                assert isinstance(data["truncated"][key], bool)


@pytest.mark.asyncio
async def test_daily_review_snapshot_outstanding_dedup(server_params, _check_env):
    """Outstanding union deduplicates by task id across the two source buckets."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("daily_review_snapshot", {})
            data = _parse_result(result)
            ids = [t["id"] for t in data["outstanding"]]
            assert len(ids) == len(set(ids)), "outstanding must be deduplicated by id"


@pytest.mark.asyncio
async def test_bulk_update_tasks_empty(server_params, _check_env):
    """bulk_update_tasks with an empty list returns an empty result and zero summary."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("bulk_update_tasks", {"updates": []})
            data = _parse_result(result)
            assert isinstance(data, dict)
            assert data["results"] == []
            assert data["summary"] == {"ok": 0, "failed": 0, "total": 0}


@pytest.mark.asyncio
async def test_bulk_update_tasks_partial_failure(server_params, _check_env):
    """A bogus task_id returns ok=false; other valid rows still succeed."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            # Find a real task to use for the success row
            tasks_result = await session.call_tool("search_tasks", {"limit": 1})
            tasks = _parse_result(tasks_result)
            if not isinstance(tasks, list) or not tasks:
                pytest.skip("No tasks available to exercise bulk_update success path")

            real_id = tasks[0]["id"]
            current_my_day = bool(tasks[0].get("my_day", False))

            updates = [
                {"task_id": real_id, "my_day": current_my_day},  # idempotent no-op
                {"task_id": "00000000-0000-0000-0000-000000000000", "my_day": True},
            ]
            result = await session.call_tool("bulk_update_tasks", {"updates": updates})
            data = _parse_result(result)
            assert data["summary"]["total"] == 2
            assert data["summary"]["ok"] >= 1
            assert data["summary"]["failed"] >= 1
            # The bogus row must surface a structured error, not raise
            bogus_row = next(
                r for r in data["results"] if r["task_id"] == "00000000-0000-0000-0000-000000000000"
            )
            assert bogus_row["ok"] is False
            assert "error" in bogus_row and bogus_row["error"]
