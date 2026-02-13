# MCP Server for Ultimate Brain

An MCP server for managing Thomas Frank's Ultimate Brain Notion system. Provides 26 workflow-oriented tools for Tasks, Projects, Notes, Tags, and Goals using the PARA methodology.

## Setup

1. Create a [Notion integration](https://www.notion.so/my-integrations) and share your Ultimate Brain databases with it.

2. Auto-discover data source IDs:

```bash
uv run python setup_dev.py
```

3. Run the server:

```bash
uv run ultimate-brain-mcp
```

## Claude Code Integration

```bash
claude mcp add ultimate-brain \
  -e NOTION_INTEGRATION_SECRET=secret_... \
  -e UB_TASKS_DS_ID=... \
  -e UB_PROJECTS_DS_ID=... \
  -e UB_NOTES_DS_ID=... \
  -e UB_TAGS_DS_ID=... \
  -e UB_GOALS_DS_ID=... \
  -- uvx ultimate-brain-mcp
```

## Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ultimate-brain": {
      "command": "uvx",
      "args": ["ultimate-brain-mcp"],
      "env": {
        "NOTION_INTEGRATION_SECRET": "secret_...",
        "UB_TASKS_DS_ID": "...",
        "UB_PROJECTS_DS_ID": "...",
        "UB_NOTES_DS_ID": "...",
        "UB_TAGS_DS_ID": "...",
        "UB_GOALS_DS_ID": "..."
      }
    }
  }
}
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
