"""FastMCP server with 28 tools for Thomas Frank's Ultimate Brain."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .config import (
    GOAL_STATUSES,
    NOTE_TYPES,
    PROJECT_STATUSES,
    TAG_TYPES,
    TASK_PRIORITIES,
    TASK_STATUSES,
    UBConfig,
)
from .formatters import (
    blocks_to_text,
    format_generic_page,
    format_goal,
    format_note,
    format_project,
    format_tag,
    format_task,
    text_to_blocks,
)
from .notion_client import NotionAPIError, NotionClient

# ---------------------------------------------------------------------------
# Lifespan context
# ---------------------------------------------------------------------------


@dataclass
class AppContext:
    client: NotionClient
    config: UBConfig


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    config = UBConfig.from_env()
    client = NotionClient(config.notion_secret)
    try:
        yield AppContext(client=client, config=config)
    finally:
        await client.close()


mcp = FastMCP(
    "Ultimate Brain",
    instructions=(
        "Tools for managing Thomas Frank's Ultimate Brain Notion system. "
        "Covers Tasks, Projects, Notes, Tags, and Goals using the PARA methodology. "
        "All search tools default to showing active/non-archived items. "
        "Use daily_summary for a quick overview of everything."
    ),
    lifespan=app_lifespan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(ctx: Context) -> AppContext:
    return ctx.request_context.lifespan_context


def _today() -> str:
    return date.today().isoformat()


def _error(msg: str) -> dict:
    return {"error": msg}


def _handle_api_error(e: NotionAPIError, hint: str = "") -> dict:
    if e.status == 404:
        msg = f"Page not found. {hint}" if hint else "Page not found."
    elif e.status == 400:
        msg = f"Invalid request: {e}. {hint}" if hint else f"Invalid request: {e}"
    elif e.status == 401:
        msg = "Authentication failed. Check NOTION_INTEGRATION_SECRET."
    elif e.status == 403:
        msg = "Permission denied. Make sure the page is shared with the integration."
    else:
        msg = f"Notion API error ({e.status}): {e}"
    return _error(msg)


# ---------------------------------------------------------------------------
# Property builders — construct Notion property values
# ---------------------------------------------------------------------------


def _prop_title(text: str) -> dict:
    return {"title": [{"text": {"content": text}}]}


def _prop_rich_text(text: str) -> dict:
    return {"rich_text": [{"text": {"content": text}}]}


def _prop_select(name: str) -> dict:
    return {"select": {"name": name}}


def _prop_multi_select(names: list[str]) -> dict:
    return {"multi_select": [{"name": n} for n in names]}


def _prop_status(name: str) -> dict:
    return {"status": {"name": name}}


def _prop_date(start: str, end: str | None = None) -> dict:
    d: dict = {"start": start}
    if end:
        d["end"] = end
    return {"date": d}


def _prop_checkbox(checked: bool) -> dict:
    return {"checkbox": checked}


def _prop_number(value: float) -> dict:
    return {"number": value}


def _prop_url(url: str) -> dict:
    return {"url": url}


def _prop_relation(ids: list[str]) -> dict:
    return {"relation": [{"id": i} for i in ids]}


# =========================================================================
#  TASKS (6 tools)
# =========================================================================


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
)
async def search_tasks(
    status: Annotated[
        str | None,
        Field(description=f"Filter by status. Options: {', '.join(TASK_STATUSES)}. Omit for non-Done tasks."),
    ] = None,
    project_id: Annotated[
        str | None,
        Field(description="Filter by project page ID."),
    ] = None,
    priority: Annotated[
        str | None,
        Field(description=f"Filter by priority. Options: {', '.join(TASK_PRIORITIES)}."),
    ] = None,
    due_before: Annotated[
        str | None,
        Field(description="Due date on or before this date (YYYY-MM-DD)."),
    ] = None,
    my_day: Annotated[
        bool | None,
        Field(description="Filter to My Day tasks only."),
    ] = None,
    query: Annotated[
        str | None,
        Field(description="Text to search for in task names."),
    ] = None,
    due_after: Annotated[
        str | None,
        Field(description="Due date on or after this date (YYYY-MM-DD). Combine with due_before for a range."),
    ] = None,
    parent_task_id: Annotated[
        str | None,
        Field(description="Filter by parent task page ID (subtasks of a specific task)."),
    ] = None,
    label: Annotated[
        str | None,
        Field(description="Filter by label name (matches tasks tagged with this label)."),
    ] = None,
    completed_before: Annotated[
        str | None,
        Field(description="Completion date on or before this date (YYYY-MM-DD). Best combined with status='Done'."),
    ] = None,
    completed_after: Annotated[
        str | None,
        Field(description="Completion date on or after this date (YYYY-MM-DD). Best combined with status='Done'."),
    ] = None,
    limit: Annotated[
        int,
        Field(description="Maximum results to return.", ge=1, le=100),
    ] = 50,
    ctx: Context = None,
) -> list[dict] | dict:
    """Search tasks by name, status, project, priority, due date, labels, parent task, or completion date.
    Defaults to non-Done tasks. Combine due_before + due_after for date ranges.
    For My Day tasks specifically, use get_my_day. For unprocessed tasks, use get_inbox_tasks."""
    app = _ctx(ctx)
    filters: list[dict] = []

    if status:
        filters.append({"property": "Status", "status": {"equals": status}})
    else:
        filters.append({"property": "Status", "status": {"does_not_equal": "Done"}})

    if project_id:
        filters.append({"property": "Project", "relation": {"contains": project_id}})
    if priority:
        filters.append({"property": "Priority", "status": {"equals": priority}})
    if due_before:
        filters.append({"property": "Due", "date": {"on_or_before": due_before}})
    if my_day is True:
        filters.append({"property": "My Day", "checkbox": {"equals": True}})
    if query:
        filters.append({"property": "Name", "title": {"contains": query}})
    if due_after:
        filters.append({"property": "Due", "date": {"on_or_after": due_after}})
    if parent_task_id:
        filters.append({"property": "Parent Task", "relation": {"contains": parent_task_id}})
    if label:
        filters.append({"property": "Labels", "multi_select": {"contains": label}})
    if completed_before:
        filters.append({"property": "Completed", "date": {"on_or_before": completed_before}})
    if completed_after:
        filters.append({"property": "Completed", "date": {"on_or_after": completed_after}})

    query_filter = {"and": filters} if len(filters) > 1 else filters[0] if filters else None
    sorts = [{"property": "Due", "direction": "ascending"}]

    try:
        pages = await app.client.query_all(
            app.config.tasks_ds_id, filter=query_filter, sorts=sorts
        )
        return [format_task(p) for p in pages[:limit]]
    except NotionAPIError as e:
        return _handle_api_error(e)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
)
async def get_my_day(
    ctx: Context = None,
) -> list[dict] | dict:
    """Get all non-Done tasks with My Day checked, sorted by priority.
    Returns task name, status, priority, and due date."""
    app = _ctx(ctx)
    query_filter = {
        "and": [
            {"property": "My Day", "checkbox": {"equals": True}},
            {"property": "Status", "status": {"does_not_equal": "Done"}},
        ]
    }
    try:
        pages = await app.client.query_all(app.config.tasks_ds_id, filter=query_filter)
        tasks = [format_task(p) for p in pages]
        priority_order = {"High": 0, "Medium": 1, "Low": 2, None: 3}
        tasks.sort(key=lambda t: priority_order.get(t.get("priority"), 3))
        return tasks
    except NotionAPIError as e:
        return _handle_api_error(e)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
)
async def get_inbox_tasks(
    ctx: Context = None,
) -> list[dict] | dict:
    """Get unprocessed inbox tasks: status is To Do, no project assigned, no due date.
    These need to be triaged — assign a project, due date, or move to a different status."""
    app = _ctx(ctx)
    query_filter = {
        "and": [
            {"property": "Status", "status": {"equals": "To Do"}},
            {"property": "Project", "relation": {"is_empty": True}},
            {"property": "Due", "date": {"is_empty": True}},
        ]
    }
    try:
        pages = await app.client.query_all(app.config.tasks_ds_id, filter=query_filter)
        return [format_task(p) for p in pages]
    except NotionAPIError as e:
        return _handle_api_error(e)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
)
async def create_task(
    name: Annotated[str, Field(description="Task name.")],
    status: Annotated[
        str | None,
        Field(description=f"Status. Options: {', '.join(TASK_STATUSES)}. Defaults to To Do."),
    ] = None,
    due: Annotated[
        str | None,
        Field(description="Due date in YYYY-MM-DD format."),
    ] = None,
    priority: Annotated[
        str | None,
        Field(description=f"Priority. Options: {', '.join(TASK_PRIORITIES)}."),
    ] = None,
    project_id: Annotated[
        str | None,
        Field(description="Project page ID to link this task to."),
    ] = None,
    labels: Annotated[
        list[str] | None,
        Field(description="Label names (multi-select)."),
    ] = None,
    my_day: Annotated[
        bool,
        Field(description="Add to My Day."),
    ] = False,
    parent_task_id: Annotated[
        str | None,
        Field(description="Parent task page ID (for sub-tasks)."),
    ] = None,
    content: Annotated[
        str | None,
        Field(description=(
            "Page body content as markdown. Supports: # headings, - bullets, "
            "1. numbered lists, - [ ] to-dos, ```code blocks```, > quotes, --- dividers, "
            "and plain paragraphs."
        )),
    ] = None,
    ctx: Context = None,
) -> dict:
    """Create a new task. Only name is required. Use search_projects to find project IDs."""
    app = _ctx(ctx)
    props: dict = {"Name": _prop_title(name)}

    if status:
        props["Status"] = _prop_status(status)
    if due:
        props["Due"] = _prop_date(due)
    if priority:
        props["Priority"] = _prop_status(priority)
    if project_id:
        props["Project"] = _prop_relation([project_id])
    if labels:
        props["Labels"] = _prop_multi_select(labels)
    if my_day:
        props["My Day"] = _prop_checkbox(True)
    if parent_task_id:
        props["Parent Task"] = _prop_relation([parent_task_id])

    children = text_to_blocks(content) if content else None

    try:
        page = await app.client.create_page(app.config.tasks_ds_id, props, children=children)
        return format_task(page)
    except NotionAPIError as e:
        return _handle_api_error(e, "Check that project/parent IDs are valid.")


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
)
async def update_task(
    task_id: Annotated[str, Field(description="Task page ID to update.")],
    name: Annotated[str | None, Field(description="New task name.")] = None,
    status: Annotated[
        str | None,
        Field(description=f"New status. Options: {', '.join(TASK_STATUSES)}."),
    ] = None,
    due: Annotated[str | None, Field(description="New due date (YYYY-MM-DD).")] = None,
    priority: Annotated[
        str | None,
        Field(description=f"New priority. Options: {', '.join(TASK_PRIORITIES)}."),
    ] = None,
    project_id: Annotated[str | None, Field(description="New project page ID.")] = None,
    labels: Annotated[list[str] | None, Field(description="New labels (replaces existing).")] = None,
    my_day: Annotated[bool | None, Field(description="Set My Day flag.")] = None,
    ctx: Context = None,
) -> dict:
    """Update any task properties. Only provided fields are changed.
    Use search_tasks to find task IDs. For completing tasks, use complete_task instead."""
    app = _ctx(ctx)
    props: dict = {}
    if name is not None:
        props["Name"] = _prop_title(name)
    if status is not None:
        props["Status"] = _prop_status(status)
    if due is not None:
        props["Due"] = _prop_date(due)
    if priority is not None:
        props["Priority"] = _prop_status(priority)
    if project_id is not None:
        props["Project"] = _prop_relation([project_id])
    if labels is not None:
        props["Labels"] = _prop_multi_select(labels)
    if my_day is not None:
        props["My Day"] = _prop_checkbox(my_day)

    if not props:
        return _error("No properties to update. Provide at least one field.")

    try:
        page = await app.client.update_page(task_id, props)
        return format_task(page)
    except NotionAPIError as e:
        return _handle_api_error(e, "Use search_tasks to find valid task IDs.")


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
)
async def complete_task(
    task_id: Annotated[str, Field(description="Task page ID to complete.")],
    ctx: Context = None,
) -> dict:
    """Mark a task as Done and set completion date to today.
    Handles recurring tasks: resets status to To Do and advances due date by the recurrence interval.
    Use search_tasks to find task IDs."""
    app = _ctx(ctx)
    try:
        page = await app.client.get_page(task_id)
        task = format_task(page)

        # Check for recurrence
        recurrence = task.get("recurrence", "")
        if recurrence:
            # Recurring task: reset to To Do and advance due date
            current_due = task.get("due")
            new_due = _advance_date(current_due, recurrence) if current_due else None
            props: dict = {"Status": _prop_status("To Do")}
            if new_due:
                props["Due"] = _prop_date(new_due)
            props["My Day"] = _prop_checkbox(False)
            page = await app.client.update_page(task_id, props)
            result = format_task(page)
            result["_note"] = f"Recurring task reset. Next due: {new_due or 'unchanged'}"
            return result
        else:
            # Non-recurring: mark Done
            props = {
                "Status": _prop_status("Done"),
                "Completed": _prop_date(_today()),
                "My Day": _prop_checkbox(False),
            }
            page = await app.client.update_page(task_id, props)
            return format_task(page)
    except NotionAPIError as e:
        return _handle_api_error(e, "Use search_tasks to find valid task IDs.")


def _advance_date(current: str, recurrence: str) -> str | None:
    """Advance a date by the recurrence interval. Supports 'every N days/weeks/months'."""
    try:
        dt = datetime.fromisoformat(current)
    except (ValueError, TypeError):
        return None

    rec = recurrence.lower().strip()
    # Parse patterns like "every 3 days", "every week", "every 2 weeks", "every month"
    import re

    match = re.match(r"every\s+(\d+)?\s*(day|week|month)s?", rec)
    if not match:
        # Default: advance by 1 week
        return (dt + timedelta(weeks=1)).date().isoformat()

    n = int(match.group(1)) if match.group(1) else 1
    unit = match.group(2)

    if unit == "day":
        new_dt = dt + timedelta(days=n)
    elif unit == "week":
        new_dt = dt + timedelta(weeks=n)
    elif unit == "month":
        # Approximate: add 30 days per month
        new_dt = dt + timedelta(days=30 * n)
    else:
        new_dt = dt + timedelta(weeks=1)

    return new_dt.date().isoformat()


# =========================================================================
#  PROJECTS (4 tools)
# =========================================================================


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
)
async def search_projects(
    status: Annotated[
        str | None,
        Field(description=f"Filter by status. Options: {', '.join(PROJECT_STATUSES)}. Omit for active projects (Doing + Ongoing)."),
    ] = None,
    tag_id: Annotated[
        str | None,
        Field(description="Filter by tag page ID."),
    ] = None,
    limit: Annotated[
        int,
        Field(description="Maximum results to return.", ge=1, le=100),
    ] = 50,
    ctx: Context = None,
) -> list[dict] | dict:
    """Search projects by status or tag. Defaults to active projects (Doing + Ongoing).
    For a full project breakdown with tasks, use get_project_detail instead."""
    app = _ctx(ctx)
    filters: list[dict] = []

    if status:
        filters.append({"property": "Status", "status": {"equals": status}})
    else:
        filters.append({
            "or": [
                {"property": "Status", "status": {"equals": "Doing"}},
                {"property": "Status", "status": {"equals": "Ongoing"}},
            ]
        })

    if tag_id:
        filters.append({"property": "Tag", "relation": {"contains": tag_id}})

    query_filter = {"and": filters} if len(filters) > 1 else filters[0]

    try:
        pages = await app.client.query_all(app.config.projects_ds_id, filter=query_filter)
        return [format_project(p) for p in pages[:limit]]
    except NotionAPIError as e:
        return _handle_api_error(e)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
)
async def get_project_detail(
    project_id: Annotated[str, Field(description="Project page ID.")],
    ctx: Context = None,
) -> dict:
    """Get a consolidated project view: properties, task breakdown by status, and recent notes.
    Use search_projects to find project IDs."""
    app = _ctx(ctx)
    try:
        # Parallel: get project, tasks, and notes
        project_fut = app.client.get_page(project_id)
        tasks_fut = app.client.query_all(
            app.config.tasks_ds_id,
            filter={"property": "Project", "relation": {"contains": project_id}},
        )
        notes_fut = app.client.query_all(
            app.config.notes_ds_id,
            filter={"property": "Project", "relation": {"contains": project_id}},
            sorts=[{"property": "Note Date", "direction": "descending"}],
        )
        project_page, task_pages, note_pages = await asyncio.gather(
            project_fut, tasks_fut, notes_fut
        )

        project = format_project(project_page)
        tasks = [format_task(t) for t in task_pages]
        notes = [format_note(n) for n in note_pages[:10]]

        # Task breakdown by status
        breakdown: dict[str, list[dict]] = {}
        for t in tasks:
            s = t.get("status", "Unknown")
            breakdown.setdefault(s, []).append(t)

        project["tasks"] = {
            "total": len(tasks),
            "by_status": {s: len(ts) for s, ts in breakdown.items()},
            "items": tasks,
        }
        project["recent_notes"] = notes
        return project
    except NotionAPIError as e:
        return _handle_api_error(e, "Use search_projects to find valid project IDs.")


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
)
async def create_project(
    name: Annotated[str, Field(description="Project name.")],
    status: Annotated[
        str | None,
        Field(description=f"Status. Options: {', '.join(PROJECT_STATUSES)}. Defaults to Not Started."),
    ] = None,
    deadline: Annotated[str | None, Field(description="Deadline in YYYY-MM-DD format.")] = None,
    tag_id: Annotated[str | None, Field(description="Tag page ID to link.")] = None,
    goal_id: Annotated[str | None, Field(description="Goal page ID to link.")] = None,
    content: Annotated[
        str | None,
        Field(description=(
            "Page body content as markdown. Supports: # headings, - bullets, "
            "1. numbered lists, - [ ] to-dos, ```code blocks```, > quotes, --- dividers, "
            "and plain paragraphs."
        )),
    ] = None,
    ctx: Context = None,
) -> dict:
    """Create a new project. Use search_tags to find tag IDs, search_goals for goal IDs."""
    app = _ctx(ctx)
    props: dict = {"Name": _prop_title(name)}
    if status:
        props["Status"] = _prop_status(status)
    if deadline:
        props["Target Deadline"] = _prop_date(deadline)
    if tag_id:
        props["Tag"] = _prop_relation([tag_id])
    if goal_id:
        props["Goal"] = _prop_relation([goal_id])

    children = text_to_blocks(content) if content else None

    try:
        page = await app.client.create_page(app.config.projects_ds_id, props, children=children)
        return format_project(page)
    except NotionAPIError as e:
        return _handle_api_error(e)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
)
async def update_project(
    project_id: Annotated[str, Field(description="Project page ID to update.")],
    name: Annotated[str | None, Field(description="New project name.")] = None,
    status: Annotated[
        str | None,
        Field(description=f"New status. Options: {', '.join(PROJECT_STATUSES)}."),
    ] = None,
    deadline: Annotated[str | None, Field(description="New deadline (YYYY-MM-DD).")] = None,
    tag_id: Annotated[str | None, Field(description="New tag page ID.")] = None,
    goal_id: Annotated[str | None, Field(description="New goal page ID.")] = None,
    ctx: Context = None,
) -> dict:
    """Update project properties. Only provided fields are changed.
    Auto-sets Completed date when status is changed to Done."""
    app = _ctx(ctx)
    props: dict = {}
    if name is not None:
        props["Name"] = _prop_title(name)
    if status is not None:
        props["Status"] = _prop_status(status)
        if status == "Done":
            props["Completed"] = _prop_date(_today())
    if deadline is not None:
        props["Target Deadline"] = _prop_date(deadline)
    if tag_id is not None:
        props["Tag"] = _prop_relation([tag_id])
    if goal_id is not None:
        props["Goal"] = _prop_relation([goal_id])

    if not props:
        return _error("No properties to update.")

    try:
        page = await app.client.update_page(project_id, props)
        return format_project(page)
    except NotionAPIError as e:
        return _handle_api_error(e, "Use search_projects to find valid project IDs.")


# =========================================================================
#  NOTES (4 tools)
# =========================================================================


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
)
async def search_notes(
    note_type: Annotated[
        str | None,
        Field(description=f"Filter by type. Options: {', '.join(NOTE_TYPES)}."),
    ] = None,
    project_id: Annotated[str | None, Field(description="Filter by project page ID.")] = None,
    tag_id: Annotated[str | None, Field(description="Filter by tag page ID.")] = None,
    favorite: Annotated[bool | None, Field(description="Filter to favorites only.")] = None,
    date_after: Annotated[str | None, Field(description="Notes on or after this date (YYYY-MM-DD).")] = None,
    query: Annotated[str | None, Field(description="Text to search for in note titles.")] = None,
    limit: Annotated[int, Field(description="Maximum results.", ge=1, le=100)] = 50,
    ctx: Context = None,
) -> list[dict] | dict:
    """Search notes by title text, type, project, tag, favorite status, or date.
    For note content/body, use get_note_content with the note ID."""
    app = _ctx(ctx)
    filters: list[dict] = []

    if note_type:
        filters.append({"property": "Type", "select": {"equals": note_type}})
    if project_id:
        filters.append({"property": "Project", "relation": {"contains": project_id}})
    if tag_id:
        filters.append({"property": "Tag", "relation": {"contains": tag_id}})
    if favorite is True:
        filters.append({"property": "Favorite", "checkbox": {"equals": True}})
    if date_after:
        filters.append({"property": "Note Date", "date": {"on_or_after": date_after}})
    if query:
        filters.append({"property": "Name", "title": {"contains": query}})

    query_filter = {"and": filters} if len(filters) > 1 else (filters[0] if filters else None)
    sorts = [{"property": "Note Date", "direction": "descending"}]

    try:
        pages = await app.client.query_all(
            app.config.notes_ds_id, filter=query_filter, sorts=sorts
        )
        return [format_note(p) for p in pages[:limit]]
    except NotionAPIError as e:
        return _handle_api_error(e)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
)
async def get_note_content(
    note_id: Annotated[str, Field(description="Note page ID.")],
    ctx: Context = None,
) -> dict:
    """Get note properties plus the full page body as plain text.
    Use search_notes to find note IDs."""
    app = _ctx(ctx)
    try:
        page_fut = app.client.get_page(note_id)
        blocks_fut = app.client.get_blocks(note_id)
        page, blocks = await asyncio.gather(page_fut, blocks_fut)

        result = format_note(page)
        result["content"] = blocks_to_text(blocks)
        return result
    except NotionAPIError as e:
        return _handle_api_error(e, "Use search_notes to find valid note IDs.")


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
)
async def create_note(
    name: Annotated[str, Field(description="Note title.")],
    note_type: Annotated[
        str | None,
        Field(description=f"Note type. Options: {', '.join(NOTE_TYPES)}."),
    ] = None,
    project_id: Annotated[str | None, Field(description="Project page ID to link.")] = None,
    tag_ids: Annotated[list[str] | None, Field(description="Tag page IDs to link.")] = None,
    source_url: Annotated[str | None, Field(description="Source URL for the note.")] = None,
    content: Annotated[
        str | None,
        Field(description=(
            "Page body content as markdown. Supports: # headings, - bullets, "
            "1. numbered lists, - [ ] to-dos, ```code blocks```, > quotes, --- dividers, "
            "and plain paragraphs."
        )),
    ] = None,
    ctx: Context = None,
) -> dict:
    """Create a new note. Use search_projects for project IDs, search_tags for tag IDs."""
    app = _ctx(ctx)
    props: dict = {
        "Name": _prop_title(name),
        "Note Date": _prop_date(_today()),
    }
    if note_type:
        props["Type"] = _prop_select(note_type)
    if project_id:
        props["Project"] = _prop_relation([project_id])
    if tag_ids:
        props["Tag"] = _prop_relation(tag_ids)
    if source_url:
        props["URL"] = _prop_url(source_url)

    children = text_to_blocks(content) if content else None

    try:
        page = await app.client.create_page(app.config.notes_ds_id, props, children=children)
        return format_note(page)
    except NotionAPIError as e:
        return _handle_api_error(e)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
)
async def update_note(
    note_id: Annotated[str, Field(description="Note page ID to update.")],
    name: Annotated[str | None, Field(description="New note title.")] = None,
    note_type: Annotated[
        str | None,
        Field(description=f"New type. Options: {', '.join(NOTE_TYPES)}."),
    ] = None,
    project_id: Annotated[str | None, Field(description="New project page ID.")] = None,
    tag_ids: Annotated[list[str] | None, Field(description="New tag page IDs (replaces existing).")] = None,
    favorite: Annotated[bool | None, Field(description="Set favorite flag.")] = None,
    source_url: Annotated[str | None, Field(description="New source URL.")] = None,
    ctx: Context = None,
) -> dict:
    """Update note properties. Only provided fields are changed."""
    app = _ctx(ctx)
    props: dict = {}
    if name is not None:
        props["Name"] = _prop_title(name)
    if note_type is not None:
        props["Type"] = _prop_select(note_type)
    if project_id is not None:
        props["Project"] = _prop_relation([project_id])
    if tag_ids is not None:
        props["Tag"] = _prop_relation(tag_ids)
    if favorite is not None:
        props["Favorite"] = _prop_checkbox(favorite)
    if source_url is not None:
        props["URL"] = _prop_url(source_url)

    if not props:
        return _error("No properties to update.")

    try:
        page = await app.client.update_page(note_id, props)
        return format_note(page)
    except NotionAPIError as e:
        return _handle_api_error(e, "Use search_notes to find valid note IDs.")


# =========================================================================
#  TAGS (3 tools)
# =========================================================================


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
)
async def search_tags(
    tag_type: Annotated[
        str | None,
        Field(description=f"Filter by PARA type. Options: {', '.join(TAG_TYPES)}."),
    ] = None,
    limit: Annotated[int, Field(description="Maximum results.", ge=1, le=100)] = 100,
    ctx: Context = None,
) -> list[dict] | dict:
    """Search tags by PARA type (Area, Resource, or Entity).
    Tags organize content across all databases in Ultimate Brain."""
    app = _ctx(ctx)
    query_filter = None
    if tag_type:
        query_filter = {"property": "Type", "status": {"equals": tag_type}}

    try:
        pages = await app.client.query_all(app.config.tags_ds_id, filter=query_filter)
        return [format_tag(p) for p in pages[:limit]]
    except NotionAPIError as e:
        return _handle_api_error(e)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
)
async def create_tag(
    name: Annotated[str, Field(description="Tag name.")],
    tag_type: Annotated[
        str | None,
        Field(description=f"PARA type. Options: {', '.join(TAG_TYPES)}."),
    ] = None,
    parent_tag_id: Annotated[str | None, Field(description="Parent tag page ID.")] = None,
    ctx: Context = None,
) -> dict:
    """Create a new tag. Tags use the PARA methodology: Area (responsibility), Resource (topic), Entity (person/place)."""
    app = _ctx(ctx)
    props: dict = {"Name": _prop_title(name)}
    if tag_type:
        props["Type"] = _prop_status(tag_type)
    if parent_tag_id:
        props["Parent Tag"] = _prop_relation([parent_tag_id])

    try:
        page = await app.client.create_page(app.config.tags_ds_id, props)
        return format_tag(page)
    except NotionAPIError as e:
        return _handle_api_error(e)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
)
async def update_tag(
    tag_id: Annotated[str, Field(description="Tag page ID to update.")],
    name: Annotated[str | None, Field(description="New tag name.")] = None,
    tag_type: Annotated[
        str | None,
        Field(description=f"New PARA type. Options: {', '.join(TAG_TYPES)}."),
    ] = None,
    parent_tag_id: Annotated[str | None, Field(description="New parent tag page ID.")] = None,
    favorite: Annotated[bool | None, Field(description="Set favorite flag.")] = None,
    ctx: Context = None,
) -> dict:
    """Update tag properties. Only provided fields are changed."""
    app = _ctx(ctx)
    props: dict = {}
    if name is not None:
        props["Name"] = _prop_title(name)
    if tag_type is not None:
        props["Type"] = _prop_status(tag_type)
    if parent_tag_id is not None:
        props["Parent Tag"] = _prop_relation([parent_tag_id])
    if favorite is not None:
        props["Favorite"] = _prop_checkbox(favorite)

    if not props:
        return _error("No properties to update.")

    try:
        page = await app.client.update_page(tag_id, props)
        return format_tag(page)
    except NotionAPIError as e:
        return _handle_api_error(e, "Use search_tags to find valid tag IDs.")


# =========================================================================
#  GOALS (4 tools)
# =========================================================================


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
)
async def search_goals(
    status: Annotated[
        str | None,
        Field(description=f"Filter by status. Options: {', '.join(GOAL_STATUSES)}. Defaults to Active."),
    ] = None,
    limit: Annotated[int, Field(description="Maximum results.", ge=1, le=100)] = 50,
    ctx: Context = None,
) -> list[dict] | dict:
    """Search goals by status. Defaults to Active goals.
    For goal details with linked projects, use get_goal_detail."""
    app = _ctx(ctx)
    if status:
        query_filter = {"property": "Status", "status": {"equals": status}}
    else:
        query_filter = {"property": "Status", "status": {"equals": "Active"}}

    try:
        pages = await app.client.query_all(app.config.goals_ds_id, filter=query_filter)
        return [format_goal(p) for p in pages[:limit]]
    except NotionAPIError as e:
        return _handle_api_error(e)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
)
async def get_goal_detail(
    goal_id: Annotated[str, Field(description="Goal page ID.")],
    ctx: Context = None,
) -> dict:
    """Get goal properties plus all linked projects with their status and progress.
    Use search_goals to find goal IDs."""
    app = _ctx(ctx)
    try:
        page = await app.client.get_page(goal_id)
        goal = format_goal(page)

        # Get linked projects
        project_ids = goal.get("project_ids", [])
        if project_ids:
            # Query projects linked to this goal
            projects_pages = await app.client.query_all(
                app.config.projects_ds_id,
                filter={"property": "Goal", "relation": {"contains": goal_id}},
            )
            goal["projects"] = [format_project(p) for p in projects_pages]
        else:
            goal["projects"] = []

        return goal
    except NotionAPIError as e:
        return _handle_api_error(e, "Use search_goals to find valid goal IDs.")


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
)
async def create_goal(
    name: Annotated[str, Field(description="Goal name.")],
    status: Annotated[
        str | None,
        Field(description=f"Status. Options: {', '.join(GOAL_STATUSES)}. Defaults to Active."),
    ] = None,
    deadline: Annotated[str | None, Field(description="Deadline in YYYY-MM-DD format.")] = None,
    tag_id: Annotated[str | None, Field(description="Tag page ID to link.")] = None,
    project_ids: Annotated[list[str] | None, Field(description="Project page IDs to link.")] = None,
    content: Annotated[
        str | None,
        Field(description=(
            "Page body content as markdown. Supports: # headings, - bullets, "
            "1. numbered lists, - [ ] to-dos, ```code blocks```, > quotes, --- dividers, "
            "and plain paragraphs."
        )),
    ] = None,
    ctx: Context = None,
) -> dict:
    """Create a new goal. Use search_tags for tag IDs, search_projects for project IDs."""
    app = _ctx(ctx)
    props: dict = {"Name": _prop_title(name)}
    if status:
        props["Status"] = _prop_status(status)
    if deadline:
        props["Target Deadline"] = _prop_date(deadline)
    if tag_id:
        props["Tag"] = _prop_relation([tag_id])
    if project_ids:
        props["Projects"] = _prop_relation(project_ids)

    children = text_to_blocks(content) if content else None

    try:
        page = await app.client.create_page(app.config.goals_ds_id, props, children=children)
        return format_goal(page)
    except NotionAPIError as e:
        return _handle_api_error(e)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
)
async def update_goal(
    goal_id: Annotated[str, Field(description="Goal page ID to update.")],
    name: Annotated[str | None, Field(description="New goal name.")] = None,
    status: Annotated[
        str | None,
        Field(description=f"New status. Options: {', '.join(GOAL_STATUSES)}."),
    ] = None,
    deadline: Annotated[str | None, Field(description="New deadline (YYYY-MM-DD).")] = None,
    tag_id: Annotated[str | None, Field(description="New tag page ID.")] = None,
    project_ids: Annotated[list[str] | None, Field(description="New project page IDs (replaces existing).")] = None,
    ctx: Context = None,
) -> dict:
    """Update goal properties. Only provided fields are changed.
    Auto-sets Achieved date when status is changed to Achieved."""
    app = _ctx(ctx)
    props: dict = {}
    if name is not None:
        props["Name"] = _prop_title(name)
    if status is not None:
        props["Status"] = _prop_status(status)
        if status == "Achieved":
            props["Achieved"] = _prop_date(_today())
    if deadline is not None:
        props["Target Deadline"] = _prop_date(deadline)
    if tag_id is not None:
        props["Tag"] = _prop_relation([tag_id])
    if project_ids is not None:
        props["Projects"] = _prop_relation(project_ids)

    if not props:
        return _error("No properties to update.")

    try:
        page = await app.client.update_page(goal_id, props)
        return format_goal(page)
    except NotionAPIError as e:
        return _handle_api_error(e, "Use search_goals to find valid goal IDs.")


# =========================================================================
#  CROSS-CUTTING (3 tools)
# =========================================================================


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
)
async def daily_summary(
    ctx: Context = None,
) -> dict:
    """Get a complete daily overview in a single call: My Day tasks, overdue tasks,
    inbox count, active projects count, and active goals count.
    Start here for a quick status check."""
    app = _ctx(ctx)
    today = _today()

    try:
        # 5 parallel queries
        my_day_fut = app.client.query_all(
            app.config.tasks_ds_id,
            filter={
                "and": [
                    {"property": "My Day", "checkbox": {"equals": True}},
                    {"property": "Status", "status": {"does_not_equal": "Done"}},
                ]
            },
        )
        overdue_fut = app.client.query_all(
            app.config.tasks_ds_id,
            filter={
                "and": [
                    {"property": "Due", "date": {"before": today}},
                    {"property": "Status", "status": {"does_not_equal": "Done"}},
                ]
            },
        )
        inbox_fut = app.client.query_all(
            app.config.tasks_ds_id,
            filter={
                "and": [
                    {"property": "Status", "status": {"equals": "To Do"}},
                    {"property": "Project", "relation": {"is_empty": True}},
                    {"property": "Due", "date": {"is_empty": True}},
                ]
            },
        )
        projects_fut = app.client.query_all(
            app.config.projects_ds_id,
            filter={
                "or": [
                    {"property": "Status", "status": {"equals": "Doing"}},
                    {"property": "Status", "status": {"equals": "Ongoing"}},
                ]
            },
        )
        goals_fut = app.client.query_all(
            app.config.goals_ds_id,
            filter={"property": "Status", "status": {"equals": "Active"}},
        )

        my_day, overdue, inbox, projects, goals = await asyncio.gather(
            my_day_fut, overdue_fut, inbox_fut, projects_fut, goals_fut
        )

        my_day_tasks = [format_task(p) for p in my_day]
        priority_order = {"High": 0, "Medium": 1, "Low": 2, None: 3}
        my_day_tasks.sort(key=lambda t: priority_order.get(t.get("priority"), 3))

        return {
            "date": today,
            "my_day": {
                "count": len(my_day_tasks),
                "tasks": my_day_tasks,
            },
            "overdue": {
                "count": len(overdue),
                "tasks": [format_task(p) for p in overdue],
            },
            "inbox_count": len(inbox),
            "active_projects_count": len(projects),
            "active_goals_count": len(goals),
        }
    except NotionAPIError as e:
        return _handle_api_error(e)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)
)
async def archive_item(
    page_id: Annotated[str, Field(description="Page ID of any Ultimate Brain item (task, project, note, tag, or goal).")],
    ctx: Context = None,
) -> dict:
    """Archive any Ultimate Brain item by setting its Archived checkbox to true.
    This is the 'delete' operation in UB — items remain in the database but are hidden
    from all dashboards and views. This action can be reversed by unchecking Archived."""
    app = _ctx(ctx)
    try:
        page = await app.client.update_page(page_id, {"Archived": _prop_checkbox(True)})
        return {"archived": True, "id": page.get("id"), "url": page.get("url", "")}
    except NotionAPIError as e:
        return _handle_api_error(e, "Check the page ID is valid.")


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)
)
async def set_page_content(
    page_id: Annotated[str, Field(description="Page ID of any Notion page.")],
    content: Annotated[
        str,
        Field(description=(
            "Page body content as markdown. Supports: # headings, - bullets, "
            "1. numbered lists, - [ ] to-dos, ```code blocks```, > quotes, --- dividers, "
            "and plain paragraphs."
        )),
    ],
    mode: Annotated[
        Literal["replace", "append"],
        Field(description="'replace' removes existing content first (default). 'append' adds after existing content."),
    ] = "replace",
    ctx: Context = None,
) -> dict:
    """Set or update the body content of any page. Use 'replace' mode to overwrite
    existing content, or 'append' to add below it. Pass empty content with 'replace'
    to clear the page body. Works on any page type (tasks, projects, notes, goals, etc.)."""
    app = _ctx(ctx)
    new_blocks = text_to_blocks(content)

    try:
        if mode == "replace":
            # Fetch existing blocks and delete them all
            existing = await app.client.get_blocks(page_id)
            if existing:
                await asyncio.gather(
                    *(app.client.delete_block(b["id"]) for b in existing)
                )

        # Append new blocks (if any)
        if new_blocks:
            await app.client.append_blocks(page_id, new_blocks)

        return {"ok": True, "page_id": page_id, "mode": mode, "blocks_written": len(new_blocks)}
    except NotionAPIError as e:
        return _handle_api_error(e, "Check the page ID is valid.")


# =========================================================================
#  GENERIC — Secondary Databases (4 tools)
# =========================================================================


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
)
async def query_database(
    database: Annotated[
        str | None,
        Field(description="Database name (e.g. 'Work Sessions', 'Books', 'People'). Omit to list available databases."),
    ] = None,
    filter: Annotated[
        dict | None,
        Field(description="Notion filter object. See Notion API docs for filter syntax."),
    ] = None,
    sorts: Annotated[
        list[dict] | None,
        Field(description="Notion sorts array. E.g. [{'property': 'Name', 'direction': 'ascending'}]"),
    ] = None,
    limit: Annotated[int, Field(description="Maximum results.", ge=1, le=100)] = 50,
    ctx: Context = None,
) -> list[dict] | dict:
    """Query any configured secondary database by name. Accepts optional Notion filter and sorts.
    Call without arguments to see which databases are available.
    For primary databases (Tasks, Projects, Notes, Tags, Goals), use the dedicated tools instead."""
    app = _ctx(ctx)

    if not database:
        available = list(app.config.secondary_ds.keys())
        if not available:
            return _error("No secondary databases configured. Set optional env vars in .env.")
        return {"available_databases": available}

    ds_id = app.config.secondary_ds.get(database)
    if not ds_id:
        available = list(app.config.secondary_ds.keys())
        return _error(
            f"Database '{database}' not found or not configured. "
            f"Available: {', '.join(available) if available else 'none'}"
        )

    try:
        pages = await app.client.query_all(ds_id, filter=filter, sorts=sorts)
        return [format_generic_page(p) for p in pages[:limit]]
    except NotionAPIError as e:
        return _handle_api_error(e)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
)
async def get_page(
    page_id: Annotated[str, Field(description="Any Notion page ID.")],
    ctx: Context = None,
) -> dict:
    """Fetch any page by ID and return all properties auto-formatted.
    Works for any database — primary or secondary."""
    app = _ctx(ctx)
    try:
        page = await app.client.get_page(page_id)
        return format_generic_page(page)
    except NotionAPIError as e:
        return _handle_api_error(e, "Check the page ID is valid.")


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
)
async def get_page_content(
    page_id: Annotated[str, Field(description="Any Notion page ID.")],
    ctx: Context = None,
) -> dict:
    """Get any page's properties plus its full body content as plain text.
    Works for any page type. For notes specifically, get_note_content returns
    the same data with note-specific formatting."""
    app = _ctx(ctx)
    try:
        page_fut = app.client.get_page(page_id)
        blocks_fut = app.client.get_blocks(page_id)
        page, blocks = await asyncio.gather(page_fut, blocks_fut)

        result = format_generic_page(page)
        result["content"] = blocks_to_text(blocks)
        return result
    except NotionAPIError as e:
        return _handle_api_error(e, "Check the page ID is valid.")


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
)
async def update_page(
    page_id: Annotated[str, Field(description="Page ID to update.")],
    properties: Annotated[
        dict,
        Field(description=(
            "Dict of property name → value. Auto-coerces types: "
            "str for title/rich_text/select/status, list[str] for multi_select, "
            "bool for checkbox, float/int for number, "
            "{'start': 'YYYY-MM-DD'} for date, list[str] for relation IDs."
        )),
    ],
    ctx: Context = None,
) -> dict:
    """Update properties on any page by ID. Accepts a dict of property name → value
    with auto type coercion. For primary database items, prefer the dedicated update tools."""
    app = _ctx(ctx)

    # First fetch the page to learn property types
    try:
        page = await app.client.get_page(page_id)
    except NotionAPIError as e:
        return _handle_api_error(e, "Check the page ID is valid.")

    existing_props = page.get("properties", {})
    notion_props: dict = {}

    for prop_name, value in properties.items():
        if prop_name not in existing_props:
            return _error(f"Property '{prop_name}' not found on this page. "
                         f"Available: {', '.join(existing_props.keys())}")

        ptype = existing_props[prop_name].get("type")
        try:
            notion_props[prop_name] = _coerce_property(ptype, value)
        except ValueError as ve:
            return _error(f"Cannot set '{prop_name}': {ve}")

    try:
        updated = await app.client.update_page(page_id, notion_props)
        return format_generic_page(updated)
    except NotionAPIError as e:
        return _handle_api_error(e)


def _coerce_property(ptype: str, value) -> dict:
    """Convert a simple value into the Notion property format based on the property type."""
    if ptype == "title":
        return _prop_title(str(value))
    elif ptype == "rich_text":
        return _prop_rich_text(str(value))
    elif ptype == "select":
        return _prop_select(str(value))
    elif ptype == "multi_select":
        if isinstance(value, list):
            return _prop_multi_select([str(v) for v in value])
        return _prop_multi_select([str(value)])
    elif ptype == "status":
        return _prop_status(str(value))
    elif ptype == "checkbox":
        return _prop_checkbox(bool(value))
    elif ptype == "number":
        return _prop_number(float(value))
    elif ptype == "date":
        if isinstance(value, dict):
            return _prop_date(value.get("start", ""), value.get("end"))
        return _prop_date(str(value))
    elif ptype == "url":
        return _prop_url(str(value))
    elif ptype == "relation":
        if isinstance(value, list):
            return _prop_relation([str(v) for v in value])
        return _prop_relation([str(value)])
    else:
        raise ValueError(f"Unsupported property type: {ptype}")
