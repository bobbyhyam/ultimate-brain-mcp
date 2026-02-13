"""Live API formatter tests — verifies formatters produce correct output from real Notion pages."""

from __future__ import annotations

import pytest

from ultimate_brain_mcp.config import UBConfig
from ultimate_brain_mcp.formatters import (
    format_generic_page,
    format_goal,
    format_note,
    format_project,
    format_tag,
    format_task,
)
from ultimate_brain_mcp.notion_client import NotionClient


@pytest.mark.asyncio
async def test_format_task(notion_client: NotionClient, ub_config: UBConfig):
    """Query tasks and verify formatter produces expected fields."""
    pages = await notion_client.query_all(ub_config.tasks_ds_id, max_pages=1)
    if not pages:
        pytest.skip("No tasks in database")

    task = format_task(pages[0])
    assert "id" in task
    assert "url" in task
    assert "name" in task
    assert "status" in task


@pytest.mark.asyncio
async def test_format_project(notion_client: NotionClient, ub_config: UBConfig, seed_project):
    """Format the seeded project and verify expected fields."""
    page = await notion_client.get_page(seed_project["id"])
    project = format_project(page)
    assert "id" in project
    assert "url" in project
    assert "name" in project
    assert project["name"] == "[TEST] Test Project"
    assert "status" in project
    assert project["status"] == "Doing"
    assert "tag_ids" in project
    assert "goal_ids" in project


@pytest.mark.asyncio
async def test_format_note(notion_client: NotionClient, ub_config: UBConfig, seed_note):
    """Format the seeded note and verify expected fields."""
    page = await notion_client.get_page(seed_note["id"])
    note = format_note(page)
    assert "id" in note
    assert "url" in note
    assert "name" in note
    assert note["name"] == "[TEST] Test Note"
    assert "type" in note
    assert note["type"] == "Note"
    assert "project_ids" in note
    assert "tag_ids" in note


@pytest.mark.asyncio
async def test_format_tag(notion_client: NotionClient, ub_config: UBConfig, seed_tag):
    """Format the seeded tag and verify expected fields."""
    page = await notion_client.get_page(seed_tag["id"])
    tag = format_tag(page)
    assert "id" in tag
    assert "url" in tag
    assert "name" in tag
    assert tag["name"] == "[TEST] Test Area Tag"
    assert "type" in tag
    assert tag["type"] == "Area"


@pytest.mark.asyncio
async def test_format_goal(notion_client: NotionClient, ub_config: UBConfig, seed_goal):
    """Format the seeded goal and verify expected fields."""
    page = await notion_client.get_page(seed_goal["id"])
    goal = format_goal(page)
    assert "id" in goal
    assert "url" in goal
    assert "name" in goal
    assert goal["name"] == "[TEST] Test Goal"
    assert "status" in goal
    assert goal["status"] == "Active"
    assert "tag_ids" in goal


@pytest.mark.asyncio
async def test_format_generic_page(notion_client: NotionClient, ub_config: UBConfig):
    """Query tasks via generic formatter and verify it extracts properties."""
    pages = await notion_client.query_all(ub_config.tasks_ds_id, max_pages=1)
    if not pages:
        pytest.skip("No tasks in database")

    generic = format_generic_page(pages[0])
    assert "id" in generic
    assert "url" in generic
    # Should have extracted at least some properties
    assert len(generic) > 2
