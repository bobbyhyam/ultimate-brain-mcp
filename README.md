# MCP Server for Ultimate Brain

An MCP server for managing Thomas Frank's Ultimate Brain Notion system. Provides 30 workflow-oriented tools for Tasks, Projects, Notes, Tags, and Goals using the PARA methodology, plus a `daily_review_snapshot` consolidator that returns everything a daily review needs in one call.

## Setup

1. Create a [Notion integration](https://www.notion.so/my-integrations) and share your Ultimate Brain databases with it.

2. Run the setup command for your client. It will auto-discover your data sources from Notion and write the config file.

### Claude Code

```bash
# Project scope (writes .mcp.json in current directory)
uvx --from ultimate-brain-mcp ultimate-brain-setup --client claude-code --scope project

# User scope (writes ~/.claude.json)
uvx --from ultimate-brain-mcp ultimate-brain-setup --client claude-code --scope user
```

### Claude Desktop

```bash
uvx --from ultimate-brain-mcp ultimate-brain-setup --client claude-desktop
```

You can also pass your Notion secret via environment variable to skip the prompt:

```bash
NOTION_INTEGRATION_SECRET=secret_... uvx --from ultimate-brain-mcp ultimate-brain-setup --client claude-code --scope project
```

## Tools

### Tasks (6)
- `search_tasks` — Filter by name, status, project, priority, due date (`due_on` for a single day), My Day, labels, parent task, completion date
- `create_task` — Create with name, status, due, priority, project, labels, tag_ids, location
- `update_task` — Patch any task properties (incl. tag_ids and location)
- `complete_task` — Mark done with recurrence handling
- `get_my_day` — My Day tasks sorted by priority
- `get_inbox_tasks` — Unprocessed tasks needing triage

### Projects (4)
- `search_projects` — Filter by status, tag
- `get_project_detail` — Properties + task breakdown + recent notes
- `create_project` — Create with name, status, deadline, tag, goal
- `update_project` — Patch project properties

### Notes (4)
- `search_notes` — Filter by type, project, tag, favorite, date
- `get_note_content` — Properties + page body as plain text
- `create_note` — Create with type, project, tags, URL
- `update_note` — Patch note properties

### Tags (3)
- `search_tags` — Filter by PARA type
- `create_tag` — Create with name, type, parent
- `update_tag` — Patch tag properties

### Goals (4)
- `search_goals` — Filter by status
- `get_goal_detail` — Properties + linked projects
- `create_goal` — Create with name, status, deadline
- `update_goal` — Patch goal properties

### Cross-Cutting (3)
- `daily_summary` — My Day, overdue, inbox, active projects/goals (counts only)
- `archive_item` — Archive any UB item
- `set_page_content` — Replace or append page body content (markdown → blocks)

### Workflow Consolidators (2)
- `daily_review_snapshot` — One call returns current time + IANA timezone, all five daily-review buckets (completed today, overdue or due today, due tomorrow, on My Day, inbox), the deduplicated outstanding union, project + area-tag lookup tables, and the live Tasks schema (Location property type, valid options, Labels options). Replaces ~7 separate read calls.
- `bulk_update_tasks` — Apply multiple task patches concurrently with per-row results. Never raises on a single failure — surfaces each failed row through the results list so the caller can retry or skip.

### Generic (4)
- `query_database` — Query any secondary database
- `get_page` — Fetch any page by ID
- `get_page_content` — Page properties plus body as plain text
- `update_page` — Update any page properties

## Configuration

Set these in `.env` (or pass via the MCP client config):

| Var | Required | Purpose |
|-----|----------|---------|
| `NOTION_INTEGRATION_SECRET` | yes | Notion integration token |
| `UB_TASKS_DS_ID`, `UB_PROJECTS_DS_ID`, `UB_NOTES_DS_ID`, `UB_TAGS_DS_ID`, `UB_GOALS_DS_ID` | yes | Primary data source IDs |
| `UB_TIMEZONE` | optional | IANA name (e.g. `Europe/London`). Used by `daily_review_snapshot` to resolve `now`/`today`/`tomorrow`. Falls back to `TZ`, then `UTC`. |
| `UB_*_DS_ID` (Work Sessions, Books, etc.) | optional | Secondary data sources surfaced via `query_database` |

## Development

```bash
uv run pytest tests/
uv run mcp dev src/ultimate_brain_mcp/server.py
```
