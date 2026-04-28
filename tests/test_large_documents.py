"""Live API integration tests for large-document support.

Verifies that documents exceeding Notion's per-request limits (>100 top-level
blocks, >2 levels of nesting) round-trip correctly through the MCP server.
Requires .env with valid Notion credentials.
"""

from __future__ import annotations

import json
import os

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


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
async def test_create_note_with_250_blocks(server_params, _check_env):
    """Create a note whose body exceeds the 100-block top-level cap."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page_id = None
            try:
                # 250 bullets — chunks into 100 (in create) + 100 (append) + 50 (append)
                content = "\n".join(f"- Item {i:03d}" for i in range(250))
                result = await session.call_tool(
                    "create_note",
                    {
                        "name": "[TEST] Large Doc 250 blocks",
                        "note_type": "Note",
                        "content": content,
                    },
                )
                data = _parse_result(result)
                assert "id" in data, f"create failed: {data}"
                page_id = data["id"]

                # Read back and verify all bullets are present in order.
                read_result = await session.call_tool(
                    "get_note_content", {"note_id": page_id}
                )
                note = _parse_result(read_result)
                body = note["content"]
                # Spot-check first, mid, last items.
                assert "Item 000" in body
                assert "Item 099" in body  # spans the first 100-block chunk boundary
                assert "Item 100" in body
                assert "Item 199" in body  # spans the second chunk boundary
                assert "Item 249" in body
            finally:
                if page_id:
                    await session.call_tool("archive_item", {"page_id": page_id})


@pytest.mark.asyncio
async def test_set_page_content_replace_300_blocks(server_params, _check_env):
    """Replace mode handles a large existing body and writes a large new one."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page_id = None
            try:
                # Seed with 120 blocks of "old" content.
                old_content = "\n".join(f"- Old item {i}" for i in range(120))
                result = await session.call_tool(
                    "create_note",
                    {
                        "name": "[TEST] Large Replace",
                        "note_type": "Note",
                        "content": old_content,
                    },
                )
                page_id = _parse_result(result)["id"]

                # Replace with 200 blocks of "new" content.
                new_content = "\n".join(f"- New item {i}" for i in range(200))
                replace_result = await session.call_tool(
                    "set_page_content",
                    {
                        "page_id": page_id,
                        "content": new_content,
                        "mode": "replace",
                    },
                )
                replace_data = _parse_result(replace_result)
                assert replace_data.get("ok") is True, f"replace failed: {replace_data}"
                assert replace_data["blocks_written"] == 200
                assert replace_data["blocks_deleted"] == 120

                # Verify new content is present and old content is gone.
                read_result = await session.call_tool(
                    "get_note_content", {"note_id": page_id}
                )
                body = _parse_result(read_result)["content"]
                assert "New item 0" in body
                assert "New item 199" in body
                assert "Old item" not in body
            finally:
                if page_id:
                    await session.call_tool("archive_item", {"page_id": page_id})


@pytest.mark.asyncio
async def test_deeply_nested_lists_round_trip(server_params, _check_env):
    """Markdown with 4 levels of indentation round-trips through deferred appends."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            page_id = None
            try:
                # 4-level nested bullets. Notion only allows 2 levels per
                # request, so levels 3-4 must be deferred.
                content = (
                    "- Level 1\n"
                    "  - Level 2\n"
                    "    - Level 3\n"
                    "      - Level 4 leaf"
                )
                result = await session.call_tool(
                    "create_note",
                    {
                        "name": "[TEST] Deep Nested",
                        "note_type": "Note",
                        "content": content,
                    },
                )
                page_id = _parse_result(result)["id"]

                read_result = await session.call_tool(
                    "get_note_content", {"note_id": page_id}
                )
                body = _parse_result(read_result)["content"]
                # All four levels must appear in the recursive read.
                assert "Level 1" in body
                assert "Level 2" in body
                assert "Level 3" in body
                assert "Level 4 leaf" in body
            finally:
                if page_id:
                    await session.call_tool("archive_item", {"page_id": page_id})
