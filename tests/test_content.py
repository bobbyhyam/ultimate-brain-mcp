"""Live API integration tests for page body content features."""

from __future__ import annotations

import json
import os

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def server_params():
    env = {**os.environ}
    return StdioServerParameters(
        command="uv",
        args=["run", "ultimate-brain-mcp"],
        env=env,
    )


@pytest.fixture(scope="session")
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


def _parse_result(result):
    texts = [c.text for c in result.content if hasattr(c, "text")]
    if not texts:
        return []
    if len(texts) == 1:
        return json.loads(texts[0])
    return [json.loads(t) for t in texts]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_note_with_content(server_params, _check_env):
    """Create a note with body content and verify it's readable."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page_id = None
            try:
                content = "# Overview\n\nThis is a test note body.\n\n- Point one\n- Point two"
                result = await session.call_tool("create_note", {
                    "name": "[TEST] Note With Content",
                    "note_type": "Note",
                    "content": content,
                })
                data = _parse_result(result)
                assert "id" in data
                page_id = data["id"]

                # Read back content
                result2 = await session.call_tool("get_note_content", {"note_id": page_id})
                note = _parse_result(result2)
                assert "content" in note
                assert "Overview" in note["content"]
                assert "test note body" in note["content"]
                assert "Point one" in note["content"]
            finally:
                if page_id:
                    await session.call_tool("archive_item", {"page_id": page_id})


@pytest.mark.asyncio
async def test_create_task_with_content(server_params, _check_env):
    """Create a task with body content and read it via get_page_content."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page_id = None
            try:
                content = "Task description paragraph.\n\n- [ ] Sub-step one\n- [x] Sub-step two"
                result = await session.call_tool("create_task", {
                    "name": "[TEST] Task With Content",
                    "content": content,
                })
                data = _parse_result(result)
                assert "id" in data
                page_id = data["id"]

                # Read back via generic get_page_content
                result2 = await session.call_tool("get_page_content", {"page_id": page_id})
                page = _parse_result(result2)
                assert "content" in page
                assert "Task description" in page["content"]
                assert "Sub-step one" in page["content"]
            finally:
                if page_id:
                    await session.call_tool("archive_item", {"page_id": page_id})


@pytest.mark.asyncio
async def test_set_page_content_replace(server_params, _check_env):
    """Replace mode overwrites existing content."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page_id = None
            try:
                # Create a bare note
                result = await session.call_tool("create_note", {
                    "name": "[TEST] Replace Content",
                    "note_type": "Note",
                })
                data = _parse_result(result)
                page_id = data["id"]

                # Set initial content
                await session.call_tool("set_page_content", {
                    "page_id": page_id,
                    "content": "Initial content paragraph.",
                    "mode": "replace",
                })

                # Verify initial content
                result2 = await session.call_tool("get_page_content", {"page_id": page_id})
                page = _parse_result(result2)
                assert "Initial content" in page["content"]

                # Replace with new content
                await session.call_tool("set_page_content", {
                    "page_id": page_id,
                    "content": "Replaced content paragraph.",
                    "mode": "replace",
                })

                # Verify replacement
                result3 = await session.call_tool("get_page_content", {"page_id": page_id})
                page2 = _parse_result(result3)
                assert "Replaced content" in page2["content"]
                assert "Initial content" not in page2["content"]
            finally:
                if page_id:
                    await session.call_tool("archive_item", {"page_id": page_id})


@pytest.mark.asyncio
async def test_set_page_content_append(server_params, _check_env):
    """Append mode adds content after existing."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page_id = None
            try:
                # Create note with initial content
                result = await session.call_tool("create_note", {
                    "name": "[TEST] Append Content",
                    "note_type": "Note",
                    "content": "Original first paragraph.",
                })
                data = _parse_result(result)
                page_id = data["id"]

                # Append more content
                await session.call_tool("set_page_content", {
                    "page_id": page_id,
                    "content": "Appended second paragraph.",
                    "mode": "append",
                })

                # Verify both are present
                result2 = await session.call_tool("get_page_content", {"page_id": page_id})
                page = _parse_result(result2)
                assert "Original first" in page["content"]
                assert "Appended second" in page["content"]
            finally:
                if page_id:
                    await session.call_tool("archive_item", {"page_id": page_id})


@pytest.mark.asyncio
async def test_set_page_content_clear(server_params, _check_env):
    """Replace with empty content clears the page body."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page_id = None
            try:
                # Create note with content
                result = await session.call_tool("create_note", {
                    "name": "[TEST] Clear Content",
                    "note_type": "Note",
                    "content": "Content to be cleared.",
                })
                data = _parse_result(result)
                page_id = data["id"]

                # Clear content
                await session.call_tool("set_page_content", {
                    "page_id": page_id,
                    "content": "",
                    "mode": "replace",
                })

                # Verify empty
                result2 = await session.call_tool("get_page_content", {"page_id": page_id})
                page = _parse_result(result2)
                assert page["content"] == ""
            finally:
                if page_id:
                    await session.call_tool("archive_item", {"page_id": page_id})


@pytest.mark.asyncio
async def test_get_page_content(server_params, _check_env):
    """get_page_content returns both properties and body text."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page_id = None
            try:
                result = await session.call_tool("create_note", {
                    "name": "[TEST] Get Page Content",
                    "note_type": "Note",
                    "content": "Body text for generic reader.",
                })
                data = _parse_result(result)
                page_id = data["id"]

                result2 = await session.call_tool("get_page_content", {"page_id": page_id})
                page = _parse_result(result2)
                # Should have properties
                assert "id" in page
                # Should have content
                assert "content" in page
                assert "Body text for generic reader" in page["content"]
            finally:
                if page_id:
                    await session.call_tool("archive_item", {"page_id": page_id})
