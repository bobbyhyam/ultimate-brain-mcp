# Building custom MCP servers for the Anthropic Agents SDK

**Purpose-built MCP servers dramatically outperform generic ones for agent reliability.** By pre-populating context, restricting tool surfaces, and writing workflow-oriented tools with precise descriptions, you can reduce token overhead by up to 98% and improve tool selection accuracy from ~49% to ~88%. This guide covers everything needed to design, build, and maintain a library of custom MCP servers for use with Claude agents - from schema design to packaging and distribution. It is written as a reference document for Claude Code when building custom MCP tools.

---

## The two SDKs you need to know

Two distinct packages form the building blocks. **The MCP Python SDK** (`pip install mcp`) provides the `FastMCP` server framework for building MCP servers. **The Claude Agent SDK** (`pip install claude-agent-sdk`) provides the agent harness that connects to those servers. The Claude Agent SDK wraps the Claude Code CLI as a subprocess and supports multiple MCP transport types simultaneously.

The Claude Agent SDK connects to MCP servers via `ClaudeAgentOptions.mcp_servers`, a dictionary mapping server names to configs. Four transport types are supported:

```python
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

options = ClaudeAgentOptions(
    mcp_servers={
        # In-process Python server (no subprocess)
        "notion": sdk_server_config,
        # Local subprocess via stdio
        "calendar": {
            "type": "stdio",
            "command": "python",
            "args": ["-m", "my_calendar_server"],
            "env": {"CALENDAR_API_KEY": "..."}
        },
        # Remote HTTP server
        "home-assistant": {
            "type": "http",
            "url": "https://ha.local:8123/mcp",
            "headers": {"Authorization": "Bearer ..."}
        },
    },
    # Whitelist specific tools (critical for production)
    allowed_tools=[
        "mcp__notion__search_tasks",
        "mcp__notion__get_project_status",
        "mcp__calendar__*",  # Wildcard: all calendar tools
    ],
    disallowed_tools=["mcp__notion__delete_database"],
)
```

**Tool naming convention** follows the pattern `mcp__<server_name>__<tool_name>`. This namespacing is essential when running multiple servers — it prevents collisions between, say, a Notion `search` tool and a MatterMost `search` tool.

For in-process servers (no subprocess overhead), use `create_sdk_mcp_server`:

```python
from claude_agent_sdk import tool, create_sdk_mcp_server

@tool("search_tasks", "Search project tasks by status or assignee", {
    "query": str, "status": str, "assignee": str
})
async def search_tasks(args: dict) -> dict:
    result = await notion_client.search(args["query"], filters={...})
    return {"content": [{"type": "text", "text": format_results(result)}]}

notion_server = create_sdk_mcp_server(
    name="notion", version="1.0.0", tools=[search_tasks]
)
```

---

## Building custom servers with FastMCP

FastMCP is the primary framework for building MCP servers. Two versions exist: **the official SDK's built-in** `mcp.server.fastmcp.FastMCP` (v1, good for most use cases) and **the standalone** `fastmcp` package (v2/v3 with advanced features like server composition, auth, and transforms). For most custom tool work, the official SDK version suffices.

### Minimal server skeleton

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "Notion Project Tracker",
    instructions="Tools for querying and updating the project tracking database. "
                 "All task queries default to the active sprint unless specified.",
)

@mcp.tool()
def search_tasks(query: str, status: str = "in_progress") -> list[dict]:
    """Search project tasks. Returns task name, assignee, status, and due date.
    Use this instead of listing all tasks — it handles filtering server-side."""
    return notion.databases.query(DATABASE_ID, filter=build_filter(query, status))

if __name__ == "__main__":
    mcp.run()  # stdio transport by default
```

Note: with the official SDK's FastMCP, use `@mcp.tool()` **with parentheses**. Without them, you get a `TypeError`. The standalone `fastmcp` package accepts either form.

### Rich parameter schemas with Pydantic

Well-typed parameters with descriptions are the single most impactful thing you can do for agent reliability. Use `Annotated` types with `Field` for parameter-level documentation:

```python
from typing import Annotated, Literal
from pydantic import Field

@mcp.tool()
def query_expenses(
    date_from: Annotated[str, Field(
        description="Start date in YYYY-MM-DD format, e.g. '2026-01-15'"
    )],
    date_to: Annotated[str, Field(
        description="End date in YYYY-MM-DD format, e.g. '2026-02-12'"
    )],
    category: Annotated[
        Literal["travel", "software", "equipment", "meals", "other"],
        Field(description="Expense category to filter by")
    ] = "other",
    limit: Annotated[int, Field(
        description="Maximum results to return", ge=1, le=100
    )] = 20,
) -> list[dict]:
    """Query expenses from the finance database. Returns amount, category,
    vendor, and approval status. Results are sorted by date descending."""
    ...
```

For complex inputs, use Pydantic models:

```python
from pydantic import BaseModel, Field

class EmailDraft(BaseModel):
    recipient: str = Field(description="Email address of the recipient")
    subject: str = Field(description="Email subject line, max 150 chars")
    body: str = Field(description="Email body in plain text")
    priority: Literal["low", "normal", "high"] = "normal"
    send_immediately: bool = Field(
        default=False,
        description="If True, sends immediately. If False, saves as draft."
    )

@mcp.tool()
def compose_email(request: EmailDraft) -> dict:
    """Compose and optionally send an email. Set send_immediately=False
    to save as draft for human review before sending."""
    ...
```

---

## Pre-populating context so the agent never discovers IDs

This is the core pattern for "highly customised" servers. **Hide infrastructure details from the LLM** — database IDs, API keys, tenant configs — using dependency injection. The agent sees only the parameters it needs to reason about.

### Pattern 1: `Depends()` for hidden parameters (standalone FastMCP v2+)

```python
from fastmcp import FastMCP
from fastmcp.dependencies import Depends

mcp = FastMCP("Notion Projects")

# --- Configuration: pre-populated, hidden from LLM ---
DATABASES = {
    "projects": "db_abc123",
    "sprints": "db_def456",
    "people": "db_ghi789",
}

def get_projects_db() -> str:
    return DATABASES["projects"]

def get_notion_client() -> NotionClient:
    return NotionClient(token=os.environ["NOTION_TOKEN"])

# --- Tool: LLM only sees 'query' and 'status' ---
@mcp.tool
def search_projects(
    query: Annotated[str, "Search term for project name or description"],
    status: Annotated[
        Literal["active", "completed", "on_hold", "all"], "Project status filter"
    ] = "active",
    db_id: str = Depends(get_projects_db),       # Hidden from schema
    client: NotionClient = Depends(get_notion_client),  # Hidden from schema
) -> list[dict]:
    """Search the projects database. Returns project name, owner, status,
    and progress percentage. Use status='all' to include archived projects."""
    return client.databases.query(db_id, filter={"status": status, "q": query})
```

### Pattern 2: Lifespan context (official MCP SDK)

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass
from mcp.server.fastmcp import FastMCP, Context

@dataclass
class AppContext:
    notion: NotionClient
    database_ids: dict[str, str]
    whoop_client: WhoopClient

@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    notion = await NotionClient.create(token=os.environ["NOTION_TOKEN"])
    whoop = await WhoopClient.create(token=os.environ["WHOOP_TOKEN"])
    try:
        yield AppContext(
            notion=notion,
            database_ids={
                "projects": "db_abc123",
                "tasks": "db_def456",
            },
            whoop=whoop,
        )
    finally:
        await notion.close()
        await whoop.close()

mcp = FastMCP("My Tools", lifespan=app_lifespan)

@mcp.tool()
def get_project_status(project_name: str, ctx: Context) -> dict:
    """Get current status, progress, and blockers for a project."""
    app = ctx.request_context.lifespan_context
    db_id = app.database_ids["projects"]
    return app.notion.databases.query(db_id, filter={"name": project_name})
```

### Pattern 3: Simple closure (zero framework dependency)

```python
# For the simplest cases, just close over your config
NOTION_DB_ID = "db_abc123"
notion = NotionClient(token=os.environ["NOTION_TOKEN"])

@mcp.tool()
def search_tasks(query: str) -> list[dict]:
    """Search tasks in the sprint backlog."""
    return notion.databases.query(NOTION_DB_ID, filter={"q": query})
```

---

## Writing tool descriptions that agents actually follow

Tool descriptions are the **most impactful lever** for agent reliability. Anthropic's internal testing showed that refining descriptions alone fixed major behavioural bugs — for instance, Claude was appending "2025" to web search queries until the tool description was improved.

### The rules

**Front-load the action.** The first words should state what the tool does. Agents may not read the full description, especially with many tools loaded.

```python
# Good: action-first, specific, disambiguating
@mcp.tool()
def search_tasks(query: str) -> list[dict]:
    """Search tasks by keyword, assignee, or status. Returns task name,
    status, assignee, and due date. For project-level overviews, use
    get_project_status instead."""

# Bad: buries the action, vague
@mcp.tool()
def query(q: str) -> list[dict]:
    """This tool can be used to look up information in the database.
    It supports various query types."""
```

**Disambiguate from sibling tools.** When your server has tools with overlapping domains, explicitly state when to use each one:

```python
@mcp.tool()
def list_recent_calls() -> list[dict]:
    """List ALL calls from the past 7 days — no filtering by user or workspace.
    To filter by specific user or workspace, use search_calls instead."""
```

**Include workflow sequencing** when tools have prerequisites:

```python
@mcp.tool()
def create_contact(name: str, email: str) -> dict:
    """Create a new contact in the CRM. Required workflow: call
    get_required_fields('contact') first to identify mandatory fields
    and prevent creation errors."""
```

**Describe the return shape** so the agent knows what data it will get:

```python
@mcp.tool()
def get_recovery_score() -> dict:
    """Get today's Whoop recovery score. Returns recovery_score (0-100),
    hrv_rms (ms), resting_hr (bpm), sleep_performance (percentage),
    and recovery_level ('green', 'yellow', 'red')."""
```

### Quantified impact of good descriptions

Anthropic's `tool_use_examples` feature (beta, November 2025) improved accuracy from **72% to 90%** on complex parameter handling by providing concrete input examples alongside schemas. Where available, add examples to tool definitions to show date formats, ID conventions, and parameter correlations.

---

## Tool filtering and per-agent tool subsets

**Never expose all tools to all agents.** Research consistently shows performance degrades as tool count increases — **58 tools across 5 servers consumed ~55K tokens** before a conversation even starts. The sweet spot per server is **20–25 tools**.

### Static filtering at configuration time

```python
# Agent 1: Project manager — only sees Notion and calendar
pm_options = ClaudeAgentOptions(
    mcp_servers={"notion": notion_server, "calendar": calendar_server},
    allowed_tools=[
        "mcp__notion__search_tasks",
        "mcp__notion__get_project_status",
        "mcp__notion__update_task_status",
        "mcp__calendar__get_availability",
        "mcp__calendar__schedule_meeting",
    ],
)

# Agent 2: Health tracker — only sees Whoop and Home Assistant
health_options = ClaudeAgentOptions(
    mcp_servers={"whoop": whoop_server, "ha": home_assistant_server},
    allowed_tools=["mcp__whoop__*", "mcp__ha__get_sensor_data"],
)
```

### Dynamic filtering with Tool Search Tool

For large tool libraries (10+ tools, 10K+ tokens of definitions), use Anthropic's **Tool Search Tool** — mark tools with `defer_loading: true` so they are discoverable on demand rather than loaded upfront. This achieved **85% token reduction** while improving accuracy (Opus 4: 49% → 74%; Opus 4.5: 79.5% → 88.1%).

Keep **3–5 most-used tools** always loaded; defer everything else.

### Server-side filtering with tags (standalone FastMCP v3)

```python
@mcp.tool(tags={"read", "notion"})
def search_projects(query: str) -> list[dict]: ...

@mcp.tool(tags={"write", "notion", "dangerous"})
def delete_project(project_id: str) -> dict: ...

# Enable/disable at runtime
mcp.enable("search_projects")
mcp.disable("delete_project")
```

### Permission control in the Claude Agent SDK

```python
async def permission_handler(tool_name, input_data, context):
    """Block destructive operations unless explicitly confirmed."""
    destructive = ["delete", "remove", "drop"]
    if any(word in tool_name for word in destructive):
        return PermissionResultDeny(
            message=f"Destructive tool '{tool_name}' requires human approval.",
            interrupt=True,  # Pauses the agent for human review
        )
    return PermissionResultAllow(updated_input=input_data)

options = ClaudeAgentOptions(
    can_use_tool=permission_handler,
    mcp_servers={...},
)
```

---

## Designing workflow-oriented tools, not API wrappers

The single most common anti-pattern is **mapping every API endpoint to a tool**. One developer found a single Fivetran MCP server with 40 endpoint-mapped tools consumed **56,912 tokens** per health check. After switching to workflow-oriented tools, token usage dropped by **98.7%**.

### The consolidation principle

Design tools that match how a human would describe the task, not how the API is structured. Each tool should handle a **complete logical unit of work**, potentially wrapping multiple API calls:

```python
# ANTI-PATTERN: Three separate tools forcing multi-step agent reasoning
@mcp.tool()
def get_customer_by_id(customer_id: str) -> dict: ...

@mcp.tool()
def list_customer_transactions(customer_id: str) -> list: ...

@mcp.tool()
def list_customer_notes(customer_id: str) -> list: ...

# GOOD PATTERN: Single tool that compiles a useful context package
@mcp.tool()
def get_customer_context(
    customer_id: Annotated[str, "Customer ID, e.g. 'cust_abc123'"],
    include_transactions: Annotated[bool, "Include recent transactions"] = True,
    include_notes: Annotated[bool, "Include support notes"] = True,
) -> dict:
    """Get comprehensive customer context including profile, recent transactions
    (last 90 days), and support notes. Returns name, email, plan, MRR,
    transaction summary, and latest 5 support notes."""
    customer = api.get_customer(customer_id)
    result = {"profile": format_profile(customer)}
    if include_transactions:
        txns = api.list_transactions(customer_id, days=90)
        result["transactions"] = summarise_transactions(txns)
    if include_notes:
        notes = api.list_notes(customer_id, limit=5)
        result["notes"] = [format_note(n) for n in notes]
    return result
```

### When to keep tools granular

- When operations are genuinely independent and frequently needed separately
- When they have different safety profiles (read vs write)
- When consolidation would make the tool's purpose ambiguous
- When the combined tool would need too many parameters (> 6–8 becomes confusing)

---

## Structuring tool responses for agent consumption

**Return only high-signal information.** Strip UUIDs, internal metadata, image dimensions, MIME types, and other noise. Resolve identifiers to human-readable names.

```python
# ANTI-PATTERN: Raw API response with noise
@mcp.tool()
def get_task(task_id: str) -> dict:
    return notion_api.get_page(task_id)  # Returns 50+ fields including internal IDs

# GOOD PATTERN: Curated, agent-friendly response
@mcp.tool()
def get_task(task_id: str) -> dict:
    """Get task details. Returns name, status, assignee name, due date,
    priority, and description summary."""
    raw = notion_api.get_page(task_id)
    return {
        "name": raw["properties"]["Name"]["title"][0]["plain_text"],
        "status": raw["properties"]["Status"]["select"]["name"],
        "assignee": resolve_user_name(raw["properties"]["Assignee"]),
        "due_date": raw["properties"]["Due"]["date"]["start"],
        "priority": raw["properties"]["Priority"]["select"]["name"],
        "description": raw["properties"]["Description"]["rich_text"][0]["plain_text"][:500],
    }
```

**Implement pagination with sensible defaults** and include steering notices:

```python
@mcp.tool()
def search_messages(
    query: str,
    channel: str | None = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
    page: int = 1,
) -> dict:
    """Search MatterMost messages. Returns message text, author, channel,
    and timestamp. Defaults to 10 results."""
    results = mattermost.search(query, channel=channel, limit=limit, offset=(page-1)*limit)
    total = results["total_count"]
    return {
        "messages": [format_message(m) for m in results["items"]],
        "showing": f"{len(results['items'])} of {total}",
        "hint": f"Use page={page+1} for more results" if total > page * limit else None,
    }
```

**Offer a `response_format` parameter** for tools returning variable amounts of data. Anthropic's testing showed concise responses used ~72 tokens versus ~206 for detailed — a **65% reduction**:

```python
@mcp.tool()
def get_portfolio_summary(
    response_format: Annotated[
        Literal["concise", "detailed"], "Level of detail in the response"
    ] = "concise",
) -> dict:
    """Get Interactive Brokers portfolio summary."""
    positions = ib_client.get_positions()
    if response_format == "concise":
        return {"total_value": sum(p.market_value for p in positions),
                "daily_pnl": sum(p.daily_pnl for p in positions),
                "position_count": len(positions)}
    return {"positions": [format_position_detail(p) for p in positions], ...}
```

---

## Error handling that helps agents recover

Error messages should answer three questions: **what happened, why, and what to do next.** Never return raw stack traces or opaque HTTP status codes.

```python
@mcp.tool()
def update_task_status(
    task_id: Annotated[str, "Task ID, e.g. 'task_abc123'"],
    new_status: Annotated[Literal["todo", "in_progress", "review", "done"], "New status"],
) -> dict:
    """Update a task's status. Valid transitions: todo→in_progress,
    in_progress→review, review→done. Use search_tasks to find task IDs."""
    try:
        result = notion.update_page(task_id, {"Status": new_status})
        return {"success": True, "task": task_id, "new_status": new_status}
    except NotionNotFoundError:
        # Actionable: tells agent what to do next
        raise ValueError(
            f"Task '{task_id}' not found. Use search_tasks to find valid task IDs. "
            f"Task IDs look like 'task_abc123'."
        )
    except NotionValidationError as e:
        raise ValueError(
            f"Invalid status transition to '{new_status}'. "
            f"Valid statuses are: todo, in_progress, review, done. "
            f"Current task status may not allow this transition."
        )
    except Exception:
        raise ValueError(
            "Failed to update task due to a server error. Try again in a moment."
        )
```

Use **MCP's `isError` flag** at the protocol level to explicitly signal failures to the LLM:

```python
from mcp.types import CallToolResult, TextContent

def handle_tool_error(message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        isError=True,  # LLM knows this is an error, not a result
    )
```

Mark tools with **safety annotations** so the agent (and any orchestration layer) can reason about risk:

```python
from mcp.types import ToolAnnotations

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
))
def send_whatsapp_message(recipient: str, message: str) -> dict:
    """Send a WhatsApp message. This action cannot be undone."""
    ...
```

---

## Architecture: one server per domain, one repo per server

**Use one MCP server per domain**, not one monolithic server. Each server owns a bounded context (Notion, Gmail, Google Calendar, Google Drive, Whoop, etc.) with 20-25 tools maximum. This gives you fault isolation, independent versioning, and clean separation of concerns.

Each server lives in its **own Git repository** - either public (for servers published to PyPI) or private (for personal/internal servers consumed via Git URL). This keeps access control clean, avoids leaking private server code, and gives each server an independent release cycle.

### Repository layout (per server)

Every server repo follows the same structure:

```
mcp-server-notion-custom/
├── pyproject.toml               # Package config, dependencies, entry point
├── uv.lock                      # Locked dependencies
├── README.md                    # Usage instructions + Claude Code/Desktop config examples
├── LICENSE
├── src/
│   └── mcp_server_notion_custom/
│       ├── __init__.py          # Entry point: main() calls mcp.run()
│       ├── server.py            # FastMCP instance + tool definitions
│       ├── client.py            # API wrapper (Notion SDK, Google API, etc.)
│       ├── config.py            # Database IDs, field mappings, constants
│       └── formatters.py        # Response formatting helpers
└── tests/
    ├── test_tools.py            # In-memory tool tests
    └── test_integration.py      # Live API tests (marked, run separately)
```

### Shared code strategy

With one repo per server, there is no shared library. Any code that would be common across servers (such as Google OAuth2 helpers used by Gmail, Calendar, and Drive) should be handled in one of two ways:

**Option A: Duplicate small utilities.** For code under ~100 lines (error handling helpers, env var validation, common Pydantic types), simply copy it into each server that needs it. The small amount of duplication is a worthwhile trade for zero cross-repo dependency management.

**Option B: Publish a tiny shared package.** For genuinely reusable code like a Google OAuth2 auth helper shared across three Google API servers, publish it as a standalone package (e.g. `mcp-google-auth`) - either to PyPI if public or to a private GitHub repo. Each server that needs it adds it as a standard dependency in `pyproject.toml`.

### Agent configuration files

Agent configs that wire together multiple servers can live in their own repo or alongside your agent application code. They reference servers by their `uvx` package names rather than local paths:

```python
# agent_configs/project_manager.py
from claude_agent_sdk import ClaudeAgentOptions

pm_options = ClaudeAgentOptions(
    system_prompt=(
        "You are a project manager assistant. You have access to Notion for "
        "task tracking, Google Calendar for scheduling, and Gmail for communications. "
        "Always check task status before scheduling related meetings."
    ),
    mcp_servers={
        # Public server from PyPI
        "notion": {
            "type": "stdio",
            "command": "uvx",
            "args": ["mcp-server-notion-custom"],
            "env": {"NOTION_TOKEN": os.environ["NOTION_TOKEN"]},
        },
        # Private server from GitHub
        "calendar": {
            "type": "stdio",
            "command": "uvx",
            "args": ["--from", "git+ssh://git@github.com/youruser/mcp-server-gcal.git", "mcp-server-gcal"],
            "env": {"GOOGLE_CREDENTIALS_PATH": os.environ["GOOGLE_CREDENTIALS_PATH"]},
        },
        # Another public server from PyPI
        "gmail": {
            "type": "stdio",
            "command": "uvx",
            "args": ["mcp-server-gmail-custom"],
            "env": {"GOOGLE_CREDENTIALS_PATH": os.environ["GOOGLE_CREDENTIALS_PATH"]},
        },
    },
    allowed_tools=[
        "mcp__notion__search_tasks",
        "mcp__notion__get_project_status",
        "mcp__notion__update_task_status",
        "mcp__calendar__get_availability",
        "mcp__calendar__schedule_meeting",
        "mcp__gmail__compose_email",
        "mcp__gmail__search_inbox",
    ],
    disallowed_tools=["mcp__notion__delete_database"],
    permission_mode="acceptEdits",
)
```

### Versioning strategy

Follow Anthropic's own convention of **date-based tool versioning** (e.g. `text_editor_20250728`). MCP supports `tools/list_changed` notifications, so servers can notify clients when tools are added, modified, or removed - enabling hot-reload without reconnection.

For package versions, use standard semver in `pyproject.toml`. Bump the minor version when adding tools, the patch version for bug fixes, and the major version for breaking changes to tool schemas.

---

## Packaging and distribution

All servers use **`uv`** for Python dependency management. This is not optional - it is the standard toolchain for the MCP Python ecosystem. Claude Desktop and Claude Code both expect to launch Python MCP servers via `uv` or `uvx`, and the official MCP Python SDK documentation assumes `uv` throughout. Using `uv` consistently from development through to distribution means your `pyproject.toml` and `uv.lock` are the single source of truth for dependencies, eliminating the common pain point of missing modules at runtime.

### Distribution strategy: public via PyPI, private via GitHub

Servers fall into two categories with different distribution paths but the same underlying tooling.

**Public servers** are published to PyPI. Consumers install and run them with a single `uvx` command - no cloning, no path management, no manual dependency resolution:

```bash
# Consumer adds your public server to Claude Code
claude mcp add notion -- uvx mcp-server-notion-custom

# Or with environment variables
claude mcp add notion -e NOTION_TOKEN=secret-xxx -- uvx mcp-server-notion-custom
```

In `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "notion": {
      "command": "uvx",
      "args": ["mcp-server-notion-custom"]
    }
  }
}
```

**Private servers** are installed directly from private GitHub repositories using `uvx` with a Git URL. This keeps the same tooling pattern as public servers while leveraging existing GitHub access controls:

```bash
# Consumer adds your private server (requires GitHub SSH access)
claude mcp add whoop -- uvx --from git+ssh://git@github.com/youruser/mcp-server-whoop.git mcp-server-whoop

# Or via HTTPS with a token
claude mcp add whoop -- uvx --from git+https://${GITHUB_TOKEN}@github.com/youruser/mcp-server-whoop.git mcp-server-whoop
```

In `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "whoop": {
      "command": "uvx",
      "args": [
        "--from", "git+ssh://git@github.com/youruser/mcp-server-whoop.git",
        "mcp-server-whoop"
      ]
    }
  }
}
```

### pyproject.toml configuration

Each server repo has a `pyproject.toml` with a console script entry point. This entry point is what `uvx` invokes when running the package:

```toml
# pyproject.toml
[project]
name = "mcp-server-notion-custom"
version = "0.1.0"
description = "Custom Notion MCP server for project tracking"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]>=1.25,<2",
    "httpx>=0.27",
    "pydantic>=2.0",
]

[project.scripts]
mcp-server-notion-custom = "mcp_server_notion_custom:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

The entry point module:

```python
# src/mcp_server_notion_custom/__init__.py
from .server import mcp

def main():
    mcp.run()
```

### Local development workflow

During development, use `uv run` from within the server's repo:

```bash
# Run the server locally
uv run mcp-server-notion-custom

# Use the MCP dev inspector with hot-reload
uv run mcp dev src/mcp_server_notion_custom/server.py

# Run tests
uv run pytest tests/

# Add a new dependency
uv add httpx
```

### Publishing public servers to PyPI

```bash
# Build the distribution
uv build

# Publish to PyPI (requires PyPI token)
uv publish --token $PYPI_TOKEN

# Consumers can now use it immediately
# claude mcp add notion -- uvx mcp-server-notion-custom
```

For automated releases, add a GitHub Action that triggers on version tags:

```yaml
# .github/workflows/publish.yml
name: Publish to PyPI
on:
  push:
    tags: ["v*"]
jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv build
      - run: uv publish --token ${{ secrets.PYPI_TOKEN }}
```

### Naming conventions

Follow the community convention for package names:

- **PyPI package name:** `mcp-server-<domain>` (e.g. `mcp-server-notion-custom`)
- **Console script:** matches the package name (e.g. `mcp-server-notion-custom`)
- **Python module:** underscored version (e.g. `mcp_server_notion_custom`)
- **MCP server name:** short domain name used in configs (e.g. `notion`)

The `-custom` suffix (or your own namespace) avoids collisions with official or community servers already on PyPI.

### Environment variables and secrets

Never bake secrets into packages. Use environment variables passed at configuration time:

```bash
# Claude Code - env vars passed at config time
claude mcp add notion -e NOTION_TOKEN=secret-xxx -e NOTION_DB_ID=db_abc123 -- uvx mcp-server-notion-custom
```

```json
// Claude Desktop - env vars in config
{
  "mcpServers": {
    "notion": {
      "command": "uvx",
      "args": ["mcp-server-notion-custom"],
      "env": {
        "NOTION_TOKEN": "secret-xxx",
        "NOTION_DB_ID": "db_abc123"
      }
    }
  }
}
```

Servers should validate required environment variables at startup and fail with a clear error message if any are missing:

```python
import os
import sys

def main():
    required = ["NOTION_TOKEN", "NOTION_DB_ID"]
    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    mcp.run()
```

---

## Testing MCP servers

### In-memory testing (no network, no subprocess)

The fastest feedback loop. Both the standalone FastMCP and the official SDK support in-memory client testing:

```python
# Using standalone FastMCP
import asyncio
from fastmcp import Client
from mcp_server_notion_custom.server import mcp

async def test_search_tasks():
    async with Client(mcp) as client:
        # Verify tool is registered
        tools = await client.list_tools()
        tool_names = [t.name for t in tools]
        assert "search_tasks" in tool_names

        # Test tool execution
        result = await client.call_tool("search_tasks", {"query": "onboarding", "status": "active"})
        assert len(result) > 0
        assert "name" in result[0]
        assert "assignee" in result[0]

        # Test error handling
        result = await client.call_tool("search_tasks", {"query": "", "status": "invalid"})
        assert result.isError

asyncio.run(test_search_tasks())
```

### MCP Inspector for interactive debugging

```bash
# Launch inspector with hot-reload
uv run mcp dev src/mcp_server_notion_custom/server.py

# CLI mode for CI/CD pipelines
npx @modelcontextprotocol/inspector --cli \
    call-tool search_tasks '{"query": "onboarding"}' \
    uv run mcp-server-notion-custom
```

### What to test

- **Schema correctness**: every tool is registered with the right name, description, and parameter types
- **Happy path**: tools return correctly formatted, agent-friendly responses
- **Error paths**: invalid inputs return actionable error messages with `isError=True`
- **Hidden parameters**: `Depends()` parameters do not appear in the tool's JSON schema
- **Integration**: actual API calls work (mark these with `@pytest.mark.integration` and run separately)

---

## Key patterns and anti-patterns reference card

### Patterns to follow

- **One server per domain**, 20–25 tools maximum per server
- **Workflow-oriented tools** that wrap multiple API calls into logical units
- **Pre-populate all IDs and credentials** via `Depends()`, lifespan context, or closures
- **Front-load tool descriptions** with the action verb and purpose
- **Disambiguate sibling tools** explicitly in descriptions ("use X instead of Y when…")
- **Return curated, high-signal responses** — resolve UUIDs to names, strip noise
- **Actionable error messages** that say what went wrong and how to fix it
- **Use `allowed_tools` whitelisting** in every agent configuration
- **Mark tool safety** with `readOnlyHint`, `destructiveHint`, `idempotentHint` annotations
- **Use `Literal` types** for constrained parameters (status enums, categories, formats)
- **Add `response_format` parameters** to data-heavy tools for token efficiency

### Anti-patterns to avoid

- ❌ Mapping every API endpoint to a separate tool (causes tool explosion)
- ❌ Returning raw API responses with internal IDs and metadata noise
- ❌ Vague descriptions like "Manages data" or "Executes query"
- ❌ Loading all tool definitions upfront with 10+ tools (use Tool Search Tool or filtering)
- ❌ Exposing database IDs or API keys as tool parameters
- ❌ Returning opaque errors ("Error 422") without recovery guidance
- ❌ Using spaces, dots, or brackets in tool names (breaks tokenisation)
- ❌ Having overlapping tools without explicit disambiguation
- ❌ Skipping tool annotations — agents and orchestrators need safety metadata
- ❌ Testing only manually — use in-memory `Client(mcp)` tests in CI

---

## Conclusion: the evaluation-driven workflow

The most effective approach, validated by Anthropic internally, is **evaluation-driven tool development**. Build prototype tools, wrap them in a FastMCP server, write in-memory tests, then run real agent evaluation tasks. Read the agent's chain-of-thought to understand why it chose (or failed to choose) the right tools. Iterate on descriptions, schemas, and tool granularity based on these observations.

Anthropic discovered that **pasting evaluation transcripts into Claude Code and asking it to refine the tools** outperformed both hand-written and "expert-designed" tools. The tools are the interface between your systems and the agent's reasoning — treat them with the same care you would give a well-designed API, but optimised for language model comprehension rather than human developers. Start with read-only tools, get the descriptions right, then expand to mutating operations with appropriate permission controls.