# MCP Server for Ultimate Brain

An MCP server for managing Thomas Frank's Ultimate Brain Notion system. Provides 26 workflow-oriented tools for Tasks, Projects, Notes, Tags, and Goals using the PARA methodology.

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
- `search_tasks` — Filter by status, project, priority, due date, My Day
- `create_task` — Create with name, status, due, priority, project, labels
- `update_task` — Patch any task properties
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

### Cross-Cutting (2)
- `daily_summary` — My Day, overdue, inbox, active projects/goals
- `archive_item` — Archive any UB item

### Generic (3)
- `query_database` — Query any secondary database
- `get_page` — Fetch any page by ID
- `update_page` — Update any page properties

## Development

```bash
uv run pytest tests/
uv run mcp dev src/ultimate_brain_mcp/server.py
```
