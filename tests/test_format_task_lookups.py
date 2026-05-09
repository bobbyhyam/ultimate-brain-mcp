"""Unit tests for format_task lookup-resolution behaviour (no API calls)."""

from __future__ import annotations

from ultimate_brain_mcp.formatters import format_task


def _task_page(*, project_ids: list[str] | None = None, tag_ids: list[str] | None = None) -> dict:
    """Construct a minimal Notion-shaped task page dict for unit tests."""
    properties: dict = {
        "Name": {
            "type": "title",
            "title": [{"plain_text": "Test task"}],
        },
        "Status": {"type": "status", "status": {"name": "To Do"}},
        "My Day": {"type": "checkbox", "checkbox": False},
        "Due": {"type": "date", "date": None},
        "Priority": {"type": "status", "status": None},
    }
    if project_ids is not None:
        properties["Project"] = {
            "type": "relation",
            "relation": [{"id": pid} for pid in project_ids],
        }
    if tag_ids is not None:
        properties["Tag"] = {
            "type": "relation",
            "relation": [{"id": tid} for tid in tag_ids],
        }
    return {
        "id": "task-1",
        "url": "https://www.notion.so/task-1",
        "properties": properties,
    }


def test_format_task_no_lookups_keeps_legacy_shape():
    """Without lookups, format_task returns IDs only — no resolved-name fields."""
    page = _task_page(project_ids=["proj-1"], tag_ids=["tag-1"])

    task = format_task(page)

    assert task["project_ids"] == ["proj-1"]
    assert task["tag_ids"] == ["tag-1"]
    assert "project_name" not in task
    assert "area_tag_names" not in task


def test_format_task_with_project_lookup_resolves_name():
    """When project_lookup is supplied, project_name is added alongside the IDs."""
    page = _task_page(project_ids=["proj-1"])
    project_lookup = {"proj-1": {"name": "Garden", "status": "Doing"}}

    task = format_task(page, project_lookup=project_lookup)

    assert task["project_ids"] == ["proj-1"]
    assert task["project_name"] == "Garden"


def test_format_task_with_multiple_projects_joins_names():
    """Multiple resolved project names are comma-joined."""
    page = _task_page(project_ids=["proj-1", "proj-2"])
    project_lookup = {
        "proj-1": {"name": "Garden"},
        "proj-2": {"name": "Kitchen"},
    }

    task = format_task(page, project_lookup=project_lookup)

    assert task["project_name"] == "Garden, Kitchen"


def test_format_task_with_tag_lookup_resolves_area_tag_names():
    """When tag_lookup is supplied, area_tag_names is populated."""
    page = _task_page(tag_ids=["tag-1", "tag-2"])
    tag_lookup = {
        "tag-1": {"name": "@home"},
        "tag-2": {"name": "@office"},
    }

    task = format_task(page, tag_lookup=tag_lookup)

    assert task["tag_ids"] == ["tag-1", "tag-2"]
    assert task["area_tag_names"] == ["@home", "@office"]


def test_format_task_lookups_skip_unknown_ids():
    """IDs not in the lookup map are simply skipped — no error, no None."""
    page = _task_page(project_ids=["proj-known", "proj-unknown"])
    project_lookup = {"proj-known": {"name": "Garden"}}

    task = format_task(page, project_lookup=project_lookup)

    assert task["project_ids"] == ["proj-known", "proj-unknown"]
    assert task["project_name"] == "Garden"


def test_format_task_no_relations_no_resolved_fields_even_with_lookups():
    """Tasks with no project/tag relations don't get resolved fields."""
    page = _task_page()
    project_lookup = {"proj-1": {"name": "Garden"}}
    tag_lookup = {"tag-1": {"name": "@home"}}

    task = format_task(page, project_lookup=project_lookup, tag_lookup=tag_lookup)

    assert "project_ids" not in task
    assert "project_name" not in task
    assert "tag_ids" not in task
    assert "area_tag_names" not in task
