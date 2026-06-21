"""Tests for live Notes Type discovery and validation.

Covers:
- `extract_select_options` pure helper (no Notion creds needed)
- `NOTE_TYPES` static fallback shape
- Live `get_data_source` against the user's Notes data source
- End-to-end `create_note` for previously-missing types (Idea / Voice Note / Web Clip)
- Invalid `note_type` returns an error listing the live valid set
"""

from __future__ import annotations

import json
import os
from datetime import date

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from ultimate_brain_mcp.config import (
    NOTE_TYPES,
    NOTES_TYPE_PROP,
    UBConfig,
    extract_select_options,
)
from ultimate_brain_mcp.notion_client import NotionClient

# ---------------------------------------------------------------------------
# Pure helper tests — no Notion creds required
# ---------------------------------------------------------------------------


def test_extract_select_options_happy_path():
    schema = {
        "properties": {
            "Type": {
                "type": "select",
                "select": {
                    "options": [
                        {"id": "1", "name": "Idea", "color": "blue"},
                        {"id": "2", "name": "Voice Note", "color": "green"},
                        {"id": "3", "name": "Web Clip", "color": "red"},
                    ]
                },
            }
        }
    }
    assert extract_select_options(schema, "Type") == ["Idea", "Voice Note", "Web Clip"]


def test_extract_select_options_missing_property():
    schema = {"properties": {"OtherProp": {"type": "select", "select": {"options": []}}}}
    assert extract_select_options(schema, "Type") == []


def test_extract_select_options_wrong_property_type():
    """A property that exists but isn't a select returns []."""
    schema = {
        "properties": {
            "Type": {
                "type": "status",
                "status": {"options": [{"name": "Active"}]},
            }
        }
    }
    assert extract_select_options(schema, "Type") == []


def test_extract_select_options_empty_schema():
    assert extract_select_options({}, "Type") == []
    assert extract_select_options({"properties": {}}, "Type") == []


def test_note_types_static_default_is_v3():
    """The static fallback list is UB v3.0's 13 options with 'Meeting' (not 'Meeting Notes')."""
    expected = {
        "Journal",
        "Meeting",
        "Web Clip",
        "Lecture",
        "Reference",
        "Book",
        "Idea",
        "Plan",
        "Recipe",
        "Voice Note",
        "Daily",
        "Note",
        "Brainstorm",
    }
    assert set(NOTE_TYPES) == expected
    assert "Meeting" in NOTE_TYPES
    assert "Meeting Notes" not in NOTE_TYPES
    assert len(NOTE_TYPES) == 13


# ---------------------------------------------------------------------------
# Live integration — get_data_source against the real workspace
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_get_data_source_returns_schema(notion_client: NotionClient, ub_config: UBConfig):
    """Live: GET /v1/data_sources/{id} returns a dict containing the Type select."""
    schema = await notion_client.get_data_source(ub_config.notes_ds_id)
    assert isinstance(schema, dict)
    assert "properties" in schema

    options = extract_select_options(schema, NOTES_TYPE_PROP)
    assert len(options) > 0, "Notes data source should expose some Type options"
    # UB v3.0 ships with these — workspace may have added more, but these must be present
    for required in ("Idea", "Voice Note", "Web Clip", "Meeting"):
        assert required in options, f"Expected '{required}' in live Type options: {options}"


# ---------------------------------------------------------------------------
# End-to-end MCP tool tests for previously-missing note types
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def server_params():
    env = {**os.environ}
    return StdioServerParameters(command="uv", args=["run", "ultimate-brain-mcp"], env=env)


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


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.parametrize("note_type", ["Idea", "Voice Note", "Web Clip"])
async def test_create_note_with_v3_types(server_params, _check_env, note_type):
    """create_note succeeds for types that the old 4-option enum blocked."""
    title = f"[TEST] {note_type} {date.today().isoformat()}"
    page_id: str | None = None
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            create = await session.call_tool("create_note", {"name": title, "note_type": note_type})
            data = _parse_result(create)
            assert isinstance(data, dict), f"Expected dict, got {data!r}"
            assert "error" not in data, f"create_note rejected '{note_type}': {data}"
            assert data.get("type") == note_type, (
                f"Created note's Type should be {note_type!r}, got {data.get('type')!r}"
            )
            page_id = data["id"]

            # Cleanup: archive the test note so we don't leave clutter
            if page_id:
                await session.call_tool("archive_item", {"page_id": page_id})


@pytest.mark.live
@pytest.mark.asyncio
async def test_create_note_invalid_type_returns_error_with_valid_options(server_params, _check_env):
    """An invalid note_type yields an error dict that lists the live valid options."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "create_note",
                {"name": "[TEST] should never be created", "note_type": "NotARealType"},
            )
            data = _parse_result(result)
            assert isinstance(data, dict)
            assert "error" in data, f"Expected error dict, got {data!r}"
            err = data["error"]
            # Error message must list at least one live valid option so AI clients
            # can self-correct on next call. "Idea" is in UB v3.0 default Notes
            # schema and the static fallback, so it should always be present.
            assert "Idea" in err, f"Error should list valid options. Got: {err}"


@pytest.mark.live
@pytest.mark.asyncio
async def test_search_notes_invalid_type_returns_error(server_params, _check_env):
    """search_notes also validates note_type — symmetry check with create_note."""
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "search_notes", {"note_type": "definitelynotvalid", "limit": 1}
            )
            data = _parse_result(result)
            # search_notes returns dict on error, list on success
            assert isinstance(data, dict)
            assert "error" in data
