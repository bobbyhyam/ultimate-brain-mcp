"""Notion JSON → agent-friendly dict transforms.

Per-database formatters for the 5 primary databases (Tasks, Projects, Notes, Tags, Goals)
and a generic formatter that auto-extracts all Notion property types.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Low-level property extractors
# ---------------------------------------------------------------------------


def _title(prop: dict) -> str:
    parts = prop.get("title", [])
    return "".join(p.get("plain_text", "") for p in parts)


def _rich_text(prop: dict) -> str:
    parts = prop.get("rich_text", [])
    return "".join(p.get("plain_text", "") for p in parts)


def _select(prop: dict) -> str | None:
    sel = prop.get("select")
    return sel["name"] if sel else None


def _multi_select(prop: dict) -> list[str]:
    return [s["name"] for s in prop.get("multi_select", [])]


def _status(prop: dict) -> str | None:
    st = prop.get("status")
    return st["name"] if st else None


def _date(prop: dict) -> dict | None:
    d = prop.get("date")
    if not d:
        return None
    result: dict = {"start": d.get("start")}
    if d.get("end"):
        result["end"] = d["end"]
    return result


def _date_start(prop: dict) -> str | None:
    d = prop.get("date")
    return d["start"] if d else None


def _checkbox(prop: dict) -> bool:
    return prop.get("checkbox", False)


def _number(prop: dict) -> float | None:
    return prop.get("number")


def _url(prop: dict) -> str | None:
    return prop.get("url")


def _relation(prop: dict) -> list[str]:
    return [r["id"] for r in prop.get("relation", [])]


def _formula(prop: dict) -> str | float | bool | None:
    f = prop.get("formula", {})
    ftype = f.get("type")
    if ftype == "string":
        return f.get("string")
    if ftype == "number":
        return f.get("number")
    if ftype == "boolean":
        return f.get("boolean")
    if ftype == "date":
        d = f.get("date")
        return d["start"] if d else None
    return None


def _people(prop: dict) -> list[str]:
    return [p.get("name", p.get("id", "")) for p in prop.get("people", [])]


def _files(prop: dict) -> list[str]:
    result = []
    for f in prop.get("files", []):
        if f.get("type") == "file":
            result.append(f.get("file", {}).get("url", ""))
        elif f.get("type") == "external":
            result.append(f.get("external", {}).get("url", ""))
    return [u for u in result if u]


def _created_time(prop: dict) -> str | None:
    return prop.get("created_time")


def _last_edited_time(prop: dict) -> str | None:
    return prop.get("last_edited_time")


def _page_url(page: dict) -> str:
    return page.get("url", "")


def _page_id(page: dict) -> str:
    return page.get("id", "")


# ---------------------------------------------------------------------------
# Helper: safely get a property dict from page properties
# ---------------------------------------------------------------------------


def _prop(page: dict, name: str) -> dict:
    return page.get("properties", {}).get(name, {})


# ---------------------------------------------------------------------------
# Per-database formatters
# ---------------------------------------------------------------------------


def format_task(page: dict) -> dict:
    """Format a Task page into an agent-friendly dict."""
    props = page.get("properties", {})
    result: dict = {
        "id": _page_id(page),
        "url": _page_url(page),
        "name": _title(props.get("Name", {})),
        "status": _status(props.get("Status", {})),
        "priority": _status(props.get("Priority", {})),
        "due": _date_start(props.get("Due", {})),
        "my_day": _checkbox(props.get("My Day", {})),
    }
    # Optional relations
    project_ids = _relation(props.get("Project", {}))
    if project_ids:
        result["project_ids"] = project_ids
    parent_ids = _relation(props.get("Parent Task", {}))
    if parent_ids:
        result["parent_task_ids"] = parent_ids
    labels = _multi_select(props.get("Labels", {}))
    if labels:
        result["labels"] = labels
    # Recurrence (Recur Unit select + Recur Interval number)
    recur_unit = _select(props.get("Recur Unit", {}))
    recur_interval = _number(props.get("Recur Interval", {}))
    if recur_unit:
        interval = int(recur_interval) if recur_interval else 1
        result["recurrence"] = f"every {interval} {recur_unit}"
    # Completion date
    done_date = _date_start(props.get("Completed", {}))
    if done_date:
        result["completion_date"] = done_date
    return result


def format_project(page: dict) -> dict:
    """Format a Project page into an agent-friendly dict."""
    props = page.get("properties", {})
    result: dict = {
        "id": _page_id(page),
        "url": _page_url(page),
        "name": _title(props.get("Name", {})),
        "status": _status(props.get("Status", {})),
    }
    deadline = _date_start(props.get("Target Deadline", {}))
    if deadline:
        result["deadline"] = deadline
    tag_ids = _relation(props.get("Tag", props.get("Tags", {})))
    if tag_ids:
        result["tag_ids"] = tag_ids
    goal_ids = _relation(props.get("Goal", props.get("Goals", {})))
    if goal_ids:
        result["goal_ids"] = goal_ids
    completed = _date_start(props.get("Completed", {}))
    if completed:
        result["completed_date"] = completed
    archived = _checkbox(props.get("Archived", {}))
    if archived:
        result["archived"] = True
    return result


def format_note(page: dict) -> dict:
    """Format a Note page into an agent-friendly dict."""
    props = page.get("properties", {})
    result: dict = {
        "id": _page_id(page),
        "url": _page_url(page),
        "name": _title(props.get("Name", {})),
        "type": _select(props.get("Type", {})),
    }
    note_date = _date_start(props.get("Note Date", {}))
    if note_date:
        result["date"] = note_date
    project_ids = _relation(props.get("Project", {}))
    if project_ids:
        result["project_ids"] = project_ids
    tag_ids = _relation(props.get("Tag", props.get("Tags", {})))
    if tag_ids:
        result["tag_ids"] = tag_ids
    fav = _checkbox(props.get("Favorite", {}))
    if fav:
        result["favorite"] = True
    url = _url(props.get("URL", {}))
    if url:
        result["source_url"] = url
    return result


def format_tag(page: dict) -> dict:
    """Format a Tag page into an agent-friendly dict."""
    props = page.get("properties", {})
    result: dict = {
        "id": _page_id(page),
        "url": _page_url(page),
        "name": _title(props.get("Name", {})),
        "type": _status(props.get("Type", {})),
    }
    parent_ids = _relation(props.get("Parent Tag", props.get("Parent", {})))
    if parent_ids:
        result["parent_tag_ids"] = parent_ids
    fav = _checkbox(props.get("Favorite", {}))
    if fav:
        result["favorite"] = True
    return result


def format_goal(page: dict) -> dict:
    """Format a Goal page into an agent-friendly dict."""
    props = page.get("properties", {})
    result: dict = {
        "id": _page_id(page),
        "url": _page_url(page),
        "name": _title(props.get("Name", {})),
        "status": _status(props.get("Status", {})),
    }
    deadline = _date_start(props.get("Target Deadline", {}))
    if deadline:
        result["deadline"] = deadline
    tag_ids = _relation(props.get("Tag", {}))
    if tag_ids:
        result["tag_ids"] = tag_ids
    project_ids = _relation(props.get("Projects", {}))
    if project_ids:
        result["project_ids"] = project_ids
    achieved = _date_start(props.get("Achieved", {}))
    if achieved:
        result["achieved_date"] = achieved
    return result


# ---------------------------------------------------------------------------
# Generic formatter — auto-extracts all property types
# ---------------------------------------------------------------------------

_EXTRACTORS: dict[str, callable] = {
    "title": _title,
    "rich_text": _rich_text,
    "select": _select,
    "multi_select": _multi_select,
    "status": _status,
    "date": _date,
    "checkbox": _checkbox,
    "number": _number,
    "url": _url,
    "relation": _relation,
    "formula": _formula,
    "people": _people,
    "files": _files,
    "created_time": _created_time,
    "last_edited_time": _last_edited_time,
}


def format_generic_page(page: dict) -> dict:
    """Auto-extract all properties from any Notion page. Used for secondary databases."""
    result: dict = {
        "id": _page_id(page),
        "url": _page_url(page),
    }
    for prop_name, prop_data in page.get("properties", {}).items():
        ptype = prop_data.get("type")
        extractor = _EXTRACTORS.get(ptype)
        if extractor:
            value = extractor(prop_data)
            # Skip empty/None values to keep response clean
            if value is not None and value != "" and value != [] and value != {}:
                result[prop_name] = value
    return result


# ---------------------------------------------------------------------------
# Block content → plain text
# ---------------------------------------------------------------------------


def blocks_to_text(blocks: list[dict]) -> str:
    """Convert Notion block children to plain text."""
    lines: list[str] = []
    for block in blocks:
        btype = block.get("type", "")
        bdata = block.get(btype, {})
        rich = bdata.get("rich_text", bdata.get("text", []))
        if isinstance(rich, list):
            text = "".join(r.get("plain_text", "") for r in rich)
        else:
            text = ""

        if btype.startswith("heading_"):
            level = btype[-1]
            lines.append(f"{'#' * int(level)} {text}")
        elif btype in ("bulleted_list_item", "numbered_list_item"):
            lines.append(f"- {text}")
        elif btype == "to_do":
            checked = bdata.get("checked", False)
            marker = "[x]" if checked else "[ ]"
            lines.append(f"- {marker} {text}")
        elif btype == "code":
            lang = bdata.get("language", "")
            lines.append(f"```{lang}\n{text}\n```")
        elif btype == "divider":
            lines.append("---")
        elif btype == "toggle":
            lines.append(f"> {text}")
        elif btype == "callout":
            lines.append(f"> {text}")
        elif btype == "quote":
            lines.append(f"> {text}")
        elif text:
            lines.append(text)

    return "\n".join(lines)
