"""Notion JSON → agent-friendly dict transforms (and reverse: text → Notion blocks).

Per-database formatters for the 5 primary databases (Tasks, Projects, Notes, Tags, Goals)
and a generic formatter that auto-extracts all Notion property types.
"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt

_md_inline = MarkdownIt().enable("strikethrough")


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


def blocks_to_text(blocks: list[dict], *, _indent: int = 0) -> str:
    """Convert Notion block children to plain text."""
    lines: list[str] = []
    prefix = "  " * _indent
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
            lines.append(f"{prefix}{'#' * int(level)} {text}")
        elif btype == "bulleted_list_item":
            lines.append(f"{prefix}- {text}")
        elif btype == "numbered_list_item":
            lines.append(f"{prefix}1. {text}")
        elif btype == "to_do":
            checked = bdata.get("checked", False)
            marker = "[x]" if checked else "[ ]"
            lines.append(f"{prefix}- {marker} {text}")
        elif btype == "code":
            lang = bdata.get("language", "")
            lines.append(f"{prefix}```{lang}\n{text}\n```")
        elif btype == "divider":
            lines.append(f"{prefix}---")
        elif btype == "toggle":
            lines.append(f"{prefix}> {text}")
        elif btype == "callout":
            lines.append(f"{prefix}> {text}")
        elif btype == "quote":
            lines.append(f"{prefix}> {text}")
        elif text:
            lines.append(f"{prefix}{text}")

        # Recurse into children (nested list items, etc.)
        children = bdata.get("children", [])
        if children:
            lines.append(blocks_to_text(children, _indent=_indent + 1))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Text → Notion blocks (inverse of blocks_to_text)
# ---------------------------------------------------------------------------

_NOTION_TEXT_LIMIT = 2000


def _plain_segment(content: str, annotations: dict | None = None, link_url: str | None = None) -> dict:
    """Create a single rich_text segment with optional annotations and link."""
    seg: dict = {
        "type": "text",
        "text": {"content": content},
        "plain_text": content,
    }
    if link_url:
        seg["text"]["link"] = {"url": link_url}
    if annotations:
        non_default = {k: v for k, v in annotations.items() if v}
        if non_default:
            seg["annotations"] = non_default
    return seg


def _walk_inline_tokens(children: list) -> list[dict]:
    """Walk markdown-it inline token children and emit Notion rich_text segments."""
    segments: list[dict] = []
    state = {"bold": False, "italic": False, "strikethrough": False}
    link_url: str | None = None

    for token in children:
        if token.type == "strong_open":
            state["bold"] = True
        elif token.type == "strong_close":
            state["bold"] = False
        elif token.type == "em_open":
            state["italic"] = True
        elif token.type == "em_close":
            state["italic"] = False
        elif token.type == "s_open":
            state["strikethrough"] = True
        elif token.type == "s_close":
            state["strikethrough"] = False
        elif token.type == "link_open":
            href = token.attrGet("href") if hasattr(token, "attrGet") else None
            if href is None and token.attrs:
                href = dict(token.attrs).get("href")
            link_url = href
        elif token.type == "link_close":
            link_url = None
        elif token.type == "code_inline":
            segments.append(_plain_segment(token.content, {"code": True}))
        elif token.type in ("text", "softbreak"):
            content = token.content if token.type == "text" else "\n"
            if content:
                annotations = {k: v for k, v in state.items() if v}
                segments.append(_plain_segment(content, annotations, link_url))

    return segments


def _chunk_segments(segments: list[dict]) -> list[dict]:
    """Split any segment with content > 2000 chars into sub-segments preserving annotations."""
    result: list[dict] = []
    for seg in segments:
        content = seg["text"]["content"]
        if len(content) <= _NOTION_TEXT_LIMIT:
            result.append(seg)
            continue
        annotations = seg.get("annotations")
        link = seg["text"].get("link")
        link_url = link["url"] if link else None
        for i in range(0, len(content), _NOTION_TEXT_LIMIT):
            chunk = content[i:i + _NOTION_TEXT_LIMIT]
            result.append(_plain_segment(chunk, dict(annotations) if annotations else None, link_url))
    return result


def _make_rich_text(text: str, *, parse_markdown: bool = True) -> list[dict]:
    """Create a rich_text array with inline markdown formatting.

    When parse_markdown=True (default), parses **bold**, *italic*, `code`,
    ~~strikethrough~~, and [links](url) into Notion annotations.
    When False, treats text as plain and just chunks at 2000 chars.
    """
    if not text:
        return []

    if not parse_markdown:
        chunks: list[dict] = []
        for i in range(0, len(text), _NOTION_TEXT_LIMIT):
            chunk = text[i:i + _NOTION_TEXT_LIMIT]
            chunks.append(_plain_segment(chunk))
        return chunks

    tokens = _md_inline.parse(text)
    segments: list[dict] = []
    for token in tokens:
        if token.type == "inline" and token.children:
            segments.extend(_walk_inline_tokens(token.children))
        elif token.type == "text" or (hasattr(token, "content") and token.content):
            segments.append(_plain_segment(token.content))

    if not segments:
        return [_plain_segment(text)]

    return _chunk_segments(segments)


def _block_paragraph(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _make_rich_text(text)}}


def _block_heading(text: str, level: int) -> dict:
    btype = f"heading_{level}"
    return {"object": "block", "type": btype, btype: {"rich_text": _make_rich_text(text)}}


def _block_bulleted_list_item(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _make_rich_text(text)}}


def _block_numbered_list_item(text: str) -> dict:
    return {"object": "block", "type": "numbered_list_item", "numbered_list_item": {"rich_text": _make_rich_text(text)}}


def _block_to_do(text: str, checked: bool = False) -> dict:
    return {"object": "block", "type": "to_do", "to_do": {"rich_text": _make_rich_text(text), "checked": checked}}


def _block_code(text: str, language: str = "plain text") -> dict:
    return {"object": "block", "type": "code", "code": {"rich_text": _make_rich_text(text, parse_markdown=False), "language": language}}


def _block_quote(text: str) -> dict:
    return {"object": "block", "type": "quote", "quote": {"rich_text": _make_rich_text(text)}}


def _block_divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def text_to_blocks(text: str) -> list[dict]:
    """Parse markdown-like text into a list of Notion block dicts.

    Supported syntax:
        # Heading 1 / ## Heading 2 / ### Heading 3
        - bullet item (supports nesting with 2-space indent)
        1. numbered item
        - [ ] unchecked to-do / - [x] checked to-do
        ```lang ... ```  (code block)
        > blockquote
        ---  (divider)
        plain text (consecutive non-blank lines joined into one paragraph)
    """
    if not text or not text.strip():
        return []

    blocks: list[dict] = []
    lines = text.split("\n")
    i = 0
    para_lines: list[str] = []  # accumulate consecutive plain-text lines

    def _flush_para():
        if para_lines:
            blocks.append(_block_paragraph("\n".join(para_lines)))
            para_lines.clear()

    def _collect_children(start: int) -> tuple[list[dict], int]:
        """Collect indented child lines starting at *start* and return (child_blocks, next_i)."""
        child_lines: list[str] = []
        j = start
        while j < len(lines):
            ln = lines[j]
            # Accept lines indented by 2+ spaces (strip exactly 2 leading spaces)
            if ln.startswith("  ") and ln[2:].strip():
                child_lines.append(ln[2:])
                j += 1
            else:
                break
        child_blocks = text_to_blocks("\n".join(child_lines)) if child_lines else []
        return child_blocks, j

    while i < len(lines):
        line = lines[i]

        # Code block (fenced)
        if line.startswith("```"):
            _flush_para()
            lang = line[3:].strip() or "plain text"
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            blocks.append(_block_code("\n".join(code_lines), lang))
            i += 1  # skip closing ```
            continue

        # Divider
        if line.strip() == "---":
            _flush_para()
            blocks.append(_block_divider())
            i += 1
            continue

        # Headings
        heading_match = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading_match:
            _flush_para()
            level = len(heading_match.group(1))
            blocks.append(_block_heading(heading_match.group(2), level))
            i += 1
            continue

        # To-do items: - [ ] or - [x]
        todo_match = re.match(r"^-\s+\[([ xX])\]\s+(.+)$", line)
        if todo_match:
            _flush_para()
            checked = todo_match.group(1).lower() == "x"
            block = _block_to_do(todo_match.group(2), checked)
            i += 1
            children, i = _collect_children(i)
            if children:
                block["to_do"]["children"] = children
            blocks.append(block)
            continue

        # Bulleted list item
        if re.match(r"^-\s+(.+)$", line):
            _flush_para()
            block = _block_bulleted_list_item(line[2:])
            i += 1
            children, i = _collect_children(i)
            if children:
                block["bulleted_list_item"]["children"] = children
            blocks.append(block)
            continue

        # Numbered list item
        num_match = re.match(r"^\d+\.\s+(.+)$", line)
        if num_match:
            _flush_para()
            block = _block_numbered_list_item(num_match.group(1))
            i += 1
            children, i = _collect_children(i)
            if children:
                block["numbered_list_item"]["children"] = children
            blocks.append(block)
            continue

        # Blockquote
        if line.startswith("> "):
            _flush_para()
            blocks.append(_block_quote(line[2:]))
            i += 1
            continue

        # Blank line → flush paragraph
        if not line.strip():
            _flush_para()
            i += 1
            continue

        # Plain text → accumulate into paragraph
        para_lines.append(line)
        i += 1

    _flush_para()
    return blocks
