# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Ultimate Brain MCP is a Python MCP (Model Context Protocol) server that exposes Thomas Frank's Ultimate Brain Notion system as 26 tools for AI assistants. Built on Anthropic's FastMCP SDK with async httpx for Notion API calls.

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
```

## Environment Setup

Requires `.env` with Notion credentials. Run `uv run python setup_dev.py` to auto-discover Notion data source IDs and generate the `.env` file. See `.env.example` for required variables.

Six required env vars: `NOTION_SECRET`, `TASKS_DS_ID`, `PROJECTS_DS_ID`, `NOTES_DS_ID`, `TAGS_DS_ID`, `GOALS_DS_ID`. Optional secondary database IDs are in `config.py:SECONDARY_DB_ENV_MAP`.

## Architecture

**Source layout:** `src/ultimate_brain_mcp/` with four modules:

- **`server.py`** — All 26 MCP tool definitions using `@mcp.tool()` decorators. Tools are grouped: Tasks (6), Projects (4), Notes (4), Tags (3), Goals (4), Cross-cutting (2), Generic (3). Each tool uses `ToolAnnotations` to declare read-only vs destructive. Property builder helpers (`_prop_title`, `_prop_select`, etc.) construct Notion API property payloads. `_coerce_property()` auto-converts Python types to Notion property format for the generic `update_page` tool.

- **`notion_client.py`** — Async httpx wrapper around Notion API v2025-09-03. Uses the data_sources query endpoint (not legacy database queries). Core methods: `query_all()` (paginated), `create_page()`, `get_page()`, `update_page()`, `get_blocks()`. Raises `NotionAPIError` with status-specific hints.

- **`formatters.py`** — Transforms raw Notion JSON into agent-friendly dicts. Per-database formatters (`format_task`, `format_project`, etc.) plus `format_generic_page` for secondary databases. Property extractors (`_title`, `_select`, `_status`, `_relation`, etc.) handle Notion's nested property format. `blocks_to_text()` converts page block content to readable text.

- **`config.py`** — `UBConfig` dataclass loaded from env vars. Defines valid statuses, priorities, tag types, and note types as constants. Maps secondary database env var names.

**Lifecycle:** `__init__.py:main()` validates env vars, then `server.py` uses an async lifespan context to create/close the `NotionClient` (stored in `mcp.ctx`).

## Testing

Tests require a live Notion workspace with valid credentials in `.env`. Tests skip gracefully if env vars are missing.

- `test_tools.py` — Spins up the MCP server via stdio client, verifies tool registration, and executes tools against the live API.
- `test_formatters.py` — Tests Notion JSON → formatted dict transformations using live data.
- `conftest.py` — Seed fixtures create items prefixed with `[TEST]` and archive them on teardown.

## Adding New Tools

1. Add tool function in `server.py` with `@mcp.tool()` and `ToolAnnotations`
2. Add formatter in `formatters.py` if the tool returns database items
3. Add constants to `config.py` if new statuses/types are needed
4. Add secondary database env vars to `SECONDARY_DB_ENV_MAP` in `config.py` — generic tools auto-discover them

## Publishing

Tag with `v*` (e.g., `git tag v0.2.0`) and push. GitHub Actions publishes to PyPI via trusted publisher (OIDC).
