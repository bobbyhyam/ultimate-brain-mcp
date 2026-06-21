"""Live API integration tests for page body content features."""

from __future__ import annotations

import json
import os

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

# Whole module exercises the live Notion API — deselected by default (-m 'not live').
pytestmark = pytest.mark.live


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


@pytest.fixture(scope="session")
def _markdown_supported(_check_env):
    """Probe once whether the page-markdown endpoints (API 2026-03-11) are
    available on this workspace; tests that require them skip otherwise."""
    import asyncio

    from ultimate_brain_mcp.notion_client import NotionAPIError, NotionClient

    async def probe():
        client = NotionClient(os.environ["NOTION_INTEGRATION_SECRET"])
        page_id = None
        try:
            page = await client.create_page(
                os.environ["UB_TASKS_DS_ID"],
                {"Name": {"title": [{"text": {"content": "[TEST] md capability probe"}}]}},
            )
            page_id = page["id"]
            try:
                await client.get_page_markdown(page_id)
                return True
            except NotionAPIError:
                return False
        finally:
            if page_id:
                await client._request("PATCH", f"/pages/{page_id}", json={"in_trash": True})
            await client.close()

    supported = asyncio.run(probe())
    if not supported:
        pytest.skip("page-markdown endpoints (Notion API 2026-03-11) not available")
    return supported


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
                result = await session.call_tool(
                    "create_note",
                    {
                        "name": "[TEST] Note With Content",
                        "note_type": "Note",
                        "content": content,
                    },
                )
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
                result = await session.call_tool(
                    "create_task",
                    {
                        "name": "[TEST] Task With Content",
                        "content": content,
                    },
                )
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
async def test_set_page_content_replace(server_params, _check_env, _markdown_supported):
    """Replace mode overwrites existing content via the markdown engine."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page_id = None
            try:
                # Create a bare note
                result = await session.call_tool(
                    "create_note",
                    {
                        "name": "[TEST] Replace Content",
                        "note_type": "Note",
                    },
                )
                data = _parse_result(result)
                page_id = data["id"]

                # Set initial content
                await session.call_tool(
                    "set_page_content",
                    {
                        "page_id": page_id,
                        "content": "Initial content paragraph.",
                        "mode": "replace",
                    },
                )

                # Verify initial content
                result2 = await session.call_tool("get_page_content", {"page_id": page_id})
                page = _parse_result(result2)
                assert "Initial content" in page["content"]

                # Replace with new content
                replace_res = _parse_result(
                    await session.call_tool(
                        "set_page_content",
                        {
                            "page_id": page_id,
                            "content": "Replaced content paragraph.",
                            "mode": "replace",
                        },
                    )
                )
                # Confirms the markdown engine ran, not the block fallback
                assert replace_res["engine"] == "markdown"

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
                result = await session.call_tool(
                    "create_note",
                    {
                        "name": "[TEST] Append Content",
                        "note_type": "Note",
                        "content": "Original first paragraph.",
                    },
                )
                data = _parse_result(result)
                page_id = data["id"]

                # Append more content
                await session.call_tool(
                    "set_page_content",
                    {
                        "page_id": page_id,
                        "content": "Appended second paragraph.",
                        "mode": "append",
                    },
                )

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
                result = await session.call_tool(
                    "create_note",
                    {
                        "name": "[TEST] Clear Content",
                        "note_type": "Note",
                        "content": "Content to be cleared.",
                    },
                )
                data = _parse_result(result)
                page_id = data["id"]

                # Clear content
                await session.call_tool(
                    "set_page_content",
                    {
                        "page_id": page_id,
                        "content": "",
                        "mode": "replace",
                    },
                )

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
                result = await session.call_tool(
                    "create_note",
                    {
                        "name": "[TEST] Get Page Content",
                        "note_type": "Note",
                        "content": "Body text for generic reader.",
                    },
                )
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


@pytest.mark.asyncio
async def test_patch_page_content_find_replace(server_params, _check_env, _markdown_supported):
    """patch_page_content applies ordered find-and-replace edits in place."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page_id = None
            try:
                result = await session.call_tool(
                    "create_note",
                    {
                        "name": "[TEST] Patch Content",
                        "note_type": "Note",
                        "content": "alpha line\nbeta line\ngamma line",
                    },
                )
                page_id = _parse_result(result)["id"]

                # Single edit + a multi-target edit in one call
                patched = await session.call_tool(
                    "patch_page_content",
                    {
                        "page_id": page_id,
                        "edits": [
                            {"old_str": "alpha", "new_str": "ALPHA"},
                            {"old_str": "beta", "new_str": "BETA"},
                        ],
                    },
                )
                pdata = _parse_result(patched)
                assert pdata.get("ok") is True
                assert pdata.get("edits_applied") == 2
                assert pdata.get("unmatched") == []

                page = _parse_result(
                    await session.call_tool("get_page_content", {"page_id": page_id})
                )
                assert "ALPHA" in page["content"]
                assert "BETA" in page["content"]
                # Untouched line and unedited tokens remain
                assert "gamma" in page["content"]
                assert "alpha line" not in page["content"]
            finally:
                if page_id:
                    await session.call_tool("archive_item", {"page_id": page_id})


@pytest.mark.asyncio
async def test_patch_page_content_replace_all_matches(
    server_params, _check_env, _markdown_supported
):
    """replace_all_matches replaces every occurrence, not just the first."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page_id = None
            try:
                result = await session.call_tool(
                    "create_note",
                    {
                        "name": "[TEST] Patch Replace All",
                        "note_type": "Note",
                        "content": "TODO one\nTODO two\nTODO three",
                    },
                )
                page_id = _parse_result(result)["id"]

                await session.call_tool(
                    "patch_page_content",
                    {
                        "page_id": page_id,
                        "edits": [
                            {"old_str": "TODO", "new_str": "DONE", "replace_all_matches": True},
                        ],
                    },
                )

                page = _parse_result(
                    await session.call_tool("get_page_content", {"page_id": page_id})
                )
                assert "TODO" not in page["content"]
                assert page["content"].count("DONE") == 3
            finally:
                if page_id:
                    await session.call_tool("archive_item", {"page_id": page_id})


@pytest.mark.asyncio
async def test_patch_page_content_validation(server_params, _check_env):
    """patch_page_content rejects empty edits and malformed edit dicts."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            # Empty edits → error payload, no exception
            empty = _parse_result(
                await session.call_tool(
                    "patch_page_content",
                    {
                        "page_id": "00000000-0000-0000-0000-000000000000",
                        "edits": [],
                    },
                )
            )
            assert empty.get("error")

            # Missing new_str → error payload
            malformed = _parse_result(
                await session.call_tool(
                    "patch_page_content",
                    {
                        "page_id": "00000000-0000-0000-0000-000000000000",
                        "edits": [{"old_str": "x"}],
                    },
                )
            )
            assert malformed.get("error")


@pytest.mark.asyncio
async def test_set_page_content_rich_block_roundtrip(
    server_params, _check_env, _markdown_supported
):
    """Tables and nested bullets survive a markdown write→read round-trip.

    This is the payoff of the server-side markdown engine — the old block
    converter could not represent tables at all.
    """
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page_id = None
            try:
                result = await session.call_tool(
                    "create_note",
                    {
                        "name": "[TEST] Rich Block Roundtrip",
                        "note_type": "Note",
                    },
                )
                page_id = _parse_result(result)["id"]

                rich = (
                    "# Heading\n\n"
                    "| Fruit | Qty |\n"
                    "|-------|-----|\n"
                    "| Apple | 3 |\n"
                    "| Pear | 7 |\n\n"
                    "- parent bullet\n"
                    "  - nested child\n"
                )
                await session.call_tool(
                    "set_page_content",
                    {
                        "page_id": page_id,
                        "content": rich,
                        "mode": "replace",
                    },
                )

                page = _parse_result(
                    await session.call_tool("get_page_content", {"page_id": page_id})
                )
                content = page["content"]
                # Table cells survived (old converter dropped tables entirely)
                assert "Apple" in content and "Pear" in content
                assert "3" in content and "7" in content
                # Heading and nested structure survived
                assert "Heading" in content
                assert "parent bullet" in content and "nested child" in content
            finally:
                if page_id:
                    await session.call_tool("archive_item", {"page_id": page_id})


@pytest.mark.asyncio
async def test_patch_page_content_partial_unmatched(server_params, _check_env, _markdown_supported):
    """A non-matching edit is reported (not silently dropped) while matching
    edits still apply — the honest contract for partial batches."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page_id = None
            try:
                result = await session.call_tool(
                    "create_note",
                    {
                        "name": "[TEST] Patch Unmatched",
                        "note_type": "Note",
                        "content": "keep this line and changeme too",
                    },
                )
                page_id = _parse_result(result)["id"]

                res = _parse_result(
                    await session.call_tool(
                        "patch_page_content",
                        {
                            "page_id": page_id,
                            "edits": [
                                {"old_str": "changeme", "new_str": "CHANGED"},
                                {"old_str": "DOES_NOT_EXIST_XYZ", "new_str": "q"},
                            ],
                        },
                    )
                )
                # One landed, one reported as unmatched; ok is False overall
                assert res["ok"] is False
                assert res["edits_applied"] == 1
                assert res["unmatched"] == [{"index": 1, "old_str": "DOES_NOT_EXIST_XYZ"}]

                page = _parse_result(
                    await session.call_tool("get_page_content", {"page_id": page_id})
                )
                assert "CHANGED" in page["content"]
                assert "changeme" not in page["content"]
            finally:
                if page_id:
                    await session.call_tool("archive_item", {"page_id": page_id})
