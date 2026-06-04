# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Ultimate Brain MCP is a Python MCP (Model Context Protocol) server that exposes Thomas Frank's Ultimate Brain Notion system as 30 tools for AI assistants. Built on Anthropic's FastMCP SDK with async httpx for Notion API calls.

## Common Commands

```bash
# Install dependencies
uv sync

# Run the MCP server
uv run ultimate-brain-mcp

# Run MCP dev inspector (interactive tool debugging)
uv run mcp dev src/ultimate_brain_mcp/server.py

# Run all tests (requires .env with valid Notion credentials)
uv run pytest tests/

# Run a single test file
uv run pytest tests/test_formatters.py

# Run a single test by name
uv run pytest tests/test_tools.py -k test_search_tasks

# Build for distribution
uv build

# Release a new version (bumps version, commits, tags, pushes)
./scripts/release.sh <major|minor|patch> "<commit message>"
./scripts/release.sh patch "Fix bug" --dry-run  # local only, no push
```

## Environment Setup

Requires `.env` with Notion credentials. Run `uv run python setup_dev.py` to auto-discover Notion data source IDs and generate the `.env` file. See `.env.example` for required variables.

Six required env vars: `NOTION_SECRET`, `TASKS_DS_ID`, `PROJECTS_DS_ID`, `NOTES_DS_ID`, `TAGS_DS_ID`, `GOALS_DS_ID`. Optional secondary database IDs are in `config.py:SECONDARY_DB_ENV_MAP`.

## Architecture

**Source layout:** `src/ultimate_brain_mcp/` with four modules:

- **`server.py`** — All 30 MCP tool definitions using `@mcp.tool()` decorators. Tools are grouped: Tasks (6), Projects (4), Notes (4), Tags (3), Goals (4), Cross-cutting (3: `daily_summary`, `archive_item`, `set_page_content`), Workflow consolidators (2: `daily_review_snapshot`, `bulk_update_tasks`), Generic (4: `query_database`, `get_page`, `get_page_content`, `update_page`). The 4 create tools (`create_task`, `create_note`, `create_project`, `create_goal`) accept an optional `content` parameter for page body content. `create_task` and `update_task` also accept optional `parent_task_id` (Parent Task relation), `tag_ids` (Tag relation), and `location` (auto-detected from the live Tasks schema). `format_task` surfaces `location` on every task read (search/get/snapshot/bulk results) when the Tasks DB has a Location property — callers pass the discovered property name from `tasks_schema`. `bulk_update_tasks` applies up to N task patches concurrently with per-row results, never raising on a single failure. Each tool uses `ToolAnnotations` to declare read-only vs destructive. Property builder helpers (`_prop_title`, `_prop_select`, `_build_location_payload`, etc.) construct Notion API property payloads. `_coerce_property()` auto-converts Python types to Notion property format for the generic `update_page` tool.

- **`notion_client.py`** — Async httpx wrapper around Notion API v2025-09-03. Uses the data_sources query endpoint (not legacy database queries). Core methods: `query_all()` (paginated), `create_page()` (supports `children` for inline body content), `get_page()`, `update_page()`, `get_blocks()`, `append_blocks()`, `delete_block()`. Raises `NotionAPIError` with status-specific hints.

- **`formatters.py`** — Transforms raw Notion JSON into agent-friendly dicts. Per-database formatters (`format_task`, `format_project`, etc.) plus `format_generic_page` for secondary databases. Property extractors (`_title`, `_select`, `_status`, `_relation`, etc.) handle Notion's nested property format. `blocks_to_text()` converts page block content to readable text. `text_to_blocks()` does the inverse — parses markdown-like text into Notion block dicts.

- **`config.py`** — `UBConfig` dataclass loaded from env vars. Defines valid statuses, priorities, tag types, and note types as constants. Maps secondary database env var names. Carries the workspace `timezone` (validated via `zoneinfo` at load time, set via `UB_TIMEZONE`). `extract_property_metadata()` introspects Notion data source schemas for select/multi_select/status options — used by lifespan-time Tasks Location + Labels discovery.

**Lifecycle:** `__init__.py:main()` validates env vars, then `server.py` uses an async lifespan context to create/close the `NotionClient` (stored in `mcp.ctx`).

## Testing

Tests require a live Notion workspace with valid credentials in `.env`. Tests skip gracefully if env vars are missing.

- `test_tools.py` — Spins up the MCP server via stdio client, verifies tool registration, and executes tools against the live API.
- `test_formatters.py` — Tests Notion JSON → formatted dict transformations using live data.
- `test_block_builders.py` — Unit tests for `text_to_blocks()` parser (no Notion credentials needed).
- `test_content.py` — Live API integration tests for page body content (create with content, set/append/clear content, get_page_content).
- `conftest.py` — Seed fixtures create items prefixed with `[TEST]` and archive them on teardown.

### End-to-end testing via Claude Code + local dev server

For exercising tools as a real MCP client (not just pytest), register this working tree as a local dev server and drive it from a headless Claude Code session in a detached tmux window:

```bash
# Register the working-tree build (project scope → .mcp.json, which is gitignored).
# `uv run --env-file` loads .env at launch — the server itself does not read .env
# (only the test suite does), so the --env-file flag is required.
claude mcp add ultimate-brain-dev --scope project -- \
  uv run --env-file "$PWD/.env" --directory "$PWD" ultimate-brain-mcp

# Run a test prompt headless in a detached tmux session, scoped to only the dev
# tools (no permission bypass). Redirect to a file and append a completion marker.
tmux new-session -d -s ub-test -c "$PWD"
tmux send-keys -t ub-test 'claude -p "$(cat /tmp/ub_test_prompt.txt)" \
  --allowedTools "mcp__ultimate-brain-dev__search_tasks mcp__ultimate-brain-dev__update_task ..." \
  > /tmp/ub_test_out.txt 2>&1; echo "===EXIT=$?===" >> /tmp/ub_test_out.txt' Enter

# Watch for completion, then read the transcript:
until grep -q '===EXIT=' /tmp/ub_test_out.txt; do sleep 2; done
```

Notes:
- Prefer `--allowedTools` over `--dangerously-skip-permissions` — whitelist the specific `mcp__ultimate-brain-dev__*` tools the test needs. The classifier blocks skip-permissions for spawned agents.
- Tools surface as `mcp__ultimate-brain-dev__<tool>` in the spawned session.
- Have the test prompt create only `[TEST]`-prefixed items and clean up afterward. Note `archive_item` requires the Tasks DB to have an `Archived` property; where it's absent, verify/clean up via the Notion API directly (a trashed page has `in_trash: true` and is excluded from `search_*` results but still resolves via `get_page`).
- Tear down with `tmux kill-session -t ub-test` and `claude mcp remove ultimate-brain-dev -s project`.

## Adding New Tools

1. Add tool function in `server.py` with `@mcp.tool()` and `ToolAnnotations`
2. Add formatter in `formatters.py` if the tool returns database items
3. Add constants to `config.py` if new statuses/types are needed
4. Add secondary database env vars to `SECONDARY_DB_ENV_MAP` in `config.py` — generic tools auto-discover them

## Publishing

Tag with `v*` (e.g., `git tag v0.2.0`) and push. GitHub Actions publishes to PyPI via trusted publisher (OIDC).
