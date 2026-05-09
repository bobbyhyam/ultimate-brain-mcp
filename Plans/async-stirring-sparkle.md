# Plan — MCP server changes to support the `daily-review` skill

## Context

The `ub` skill's `daily-review` workflow currently needs ~36 MCP tool calls per run on a typical task list (8 reads in Phase 2, ~28 writes in Phase 6). It also forces the agent to do book-keeping work the server is better positioned for: building `project_id → name` and `tag_id → name` lookup maps, computing the deduplicated outstanding union, fishing through a sample task to discover the location convention, and computing local time for the morning/evening detection.

This plan introduces two new MCP tools and three additive enhancements that collapse the workflow to **2 tool calls per run** (one read snapshot, one bulk write) while remaining backwards-compatible — every existing tool keeps its current signature and behaviour.

## Recommended approach

This section follows the tool-design best practices in `ai_docs/Custom_MCP_Servers.md` — action-first docstrings, sibling-tool disambiguation, explicit return-shape descriptions, `Literal` types for constrained params, `ToolAnnotations` for safety, and actionable per-row errors.

### Two new tools

#### 1. `daily_review_snapshot(inbox_limit: int = 100)` — read-only, idempotent

**Annotations:** `readOnlyHint=True, destructiveHint=False, idempotentHint=True`.

**Docstring (action-first, return-shape-explicit, disambiguating):**

```python
"""Get a complete daily-review snapshot in one call: current time, all five
task buckets needed for review, the deduplicated outstanding set, project and
area-tag lookup tables, and the Tasks data source schema for location handling.

Use this at the START of a daily review — replaces 7 separate read calls
(search_tasks ×4, get_inbox_tasks, search_projects, search_tags) plus the
get_page sample needed to discover the Location property.

Returns:
  now              — ISO8601 with offset, e.g. '2026-05-09T17:21:33+01:00'
  timezone         — IANA name, e.g. 'Europe/London'
  buckets:
    completed_today        — tasks marked Done with completion_date = today
    overdue_or_due_today   — non-Done tasks with due ≤ today
    due_tomorrow           — non-Done tasks due exactly tomorrow
    on_my_day              — non-Done tasks with My Day flag set (any due date)
    inbox                  — non-Done tasks with status To Do, no project, no due
  outstanding      — deduplicated union of overdue_or_due_today ∪ on_my_day
  lookups:
    projects       — {id → {name, status}} for active projects (Doing + Ongoing)
    area_tags      — {id → {name}} for tags with type=Area
  task_schema:
    has_location_property      — bool
    location_property_name     — string or null
    location_property_type     — 'select' | 'multi_select' | 'status' | null
    location_options           — string[] of valid values
    labels_options             — string[] of valid Labels multi_select values
  truncated        — {bucket_name → bool} flagging buckets that hit their cap

For just counts (no per-task details), use daily_summary instead — much smaller
response. For a single task's full content, use get_page or get_page_content."""
```

**Why one tool, not seven:** the consolidation principle. The agent reasons "I'm doing a daily review" once, not "I need completed today, then overdue, then…" seven times. Per the doc, this is the canonical workflow-oriented pattern — match how a human describes the task.

#### 2. `bulk_update_tasks(updates: list[BulkTaskUpdate])` — write, idempotent, non-destructive

**Annotations:** `readOnlyHint=False, destructiveHint=False, idempotentHint=True`.

**Pydantic model for each update item** (so the schema is fully typed and parameter docs surface in the LLM-visible schema):

```python
class BulkTaskUpdate(BaseModel):
    task_id: str = Field(description="Task page ID, e.g. 'task_abc123'.")
    name: str | None = Field(default=None, description="New task name.")
    status: Literal["To Do", "Doing", "Done"] | None = Field(default=None)
    due: str | None = Field(default=None, description="New due date, YYYY-MM-DD.")
    priority: Literal["Low", "Medium", "High"] | None = Field(default=None)
    project_id: str | None = Field(default=None, description="New project page ID.")
    labels: list[str] | None = Field(default=None, description="Replaces existing labels.")
    my_day: bool | None = Field(default=None)
    parent_task_id: str | None = Field(default=None)
    tag_ids: list[str] | None = Field(default=None, description="Replaces Tag relation.")
    location: str | None = Field(default=None, description="Sets Location property; ignored if Tasks has no Location property — see daily_review_snapshot.task_schema.")
```

**Docstring:**

```python
"""Apply multiple task patches in a single call. Each update follows the same
shape as update_task plus tag_ids and location. Runs concurrently under the
Notion rate limiter; never raises on a single failure.

Use this at the END of a daily review or any workflow that updates more than
~3 tasks at once. For a single task, use update_task instead.

Returns:
  results — list of one entry per input update, in order:
    {task_id, ok: true,  task: {formatted task dict}}     on success
    {task_id, ok: false, error: 'human-readable reason'}  on failure
  summary — {ok: N, failed: N, total: N}

Failures are per-row and self-describing — surface them to the user, retry the
failed rows, or skip them. The whole call never raises; ok=false rows are
surfaced through results, not through an exception."""
```

**Why per-row errors, not raise:** per the doc, error messages should answer what/why/what-next. Bulk operations need this granularity — one bad task_id should not abort the other 14 writes.

### Three additive enhancements

#### 3. `location` and `tag_ids` parameters on `update_task` / `create_task`

Server consults `app.tasks_schema` to construct the right Notion payload (`select`, `multi_select`, `status`). When no dedicated Location property exists, `location` is ignored — the result includes a `_warning` field telling the agent why and where to look (the snapshot's `task_schema.has_location_property`). Labels-based location is handled by the existing `labels=[...]` parameter rather than magic merging — this keeps semantics predictable.

Updated docstring snippet for `update_task`:

```python
"""Update any task properties. Only provided fields are changed.
[…]
location: sets the Tasks Location property (auto-detects select / multi_select /
  status type). Only valid when Tasks has a Location property — check
  daily_review_snapshot.task_schema.has_location_property first. If location
  lives in Labels, pass it via labels=[...] instead.

For completing tasks, use complete_task. For batch updates of >3 tasks, use
bulk_update_tasks instead — single round-trip vs N."""
```

#### 4. Resolved relation names in `format_task`

New optional `project_lookup` and `tag_lookup` kwargs on `format_task`. When passed, populates `project_name` (string; comma-joined if multiple) and `area_tag_names` (list of strings) alongside the existing `project_ids` / `tag_ids`. Default behaviour unchanged when no lookups are passed — every existing call site keeps working untouched. Used by `daily_review_snapshot` to deliver agent-friendly task entries (per the doc: "Resolve identifiers to human-readable names").

#### 5. `due_on=<YYYY-MM-DD>` shorthand on `search_tasks`

Mutually exclusive with `due_before`/`due_after`. If combined, returns:

```
{"error": "due_on cannot combine with due_before or due_after. "
          "Use due_on for a single day, or the pair for a range."}
```

Server expands `due_on=X` to `{"property": "Due", "date": {"equals": X}}`. Used internally by `daily_review_snapshot` for the "due tomorrow" bucket.

### Tool-count budget

The doc recommends 20–25 tools per server. Existing surface is 28; adding 2 brings us to 30. The new pair are workflow consolidators — they reduce calls per workflow rather than adding to the agent's selection burden. Mitigation: server `instructions=` string explicitly steers the agent to `daily_review_snapshot` for review workflows and `bulk_update_tasks` for ≥3-task batches, so tool selection stays clean. A future deprecation pass could collapse some single-bucket reads (e.g. `get_my_day`, `get_inbox_tasks`) into the snapshot, but that's out of scope for this plan.

### Workspace timezone

Add `timezone: str` field to `UBConfig`, loaded from `UB_TIMEZONE` (default: system tz, fallback `UTC`). Validated via `zoneinfo.ZoneInfo` at `from_env()` — invalid values raise immediately, same pattern as existing required-var validation.

## Files to modify

| Path | Change |
|------|--------|
| `src/ultimate_brain_mcp/config.py` | Add `timezone` field to `UBConfig`; validate via `zoneinfo`. Add `extract_property_metadata(schema, name)` helper next to existing `extract_select_options()`. |
| `src/ultimate_brain_mcp/server.py` | Add `_discover_tasks_schema()` mirroring `_discover_note_types()`. Extend `AppContext` with `tasks_schema` dataclass. Add `due_on` to `search_tasks`; add `location` + `tag_ids` to `update_task`/`create_task`. Register new `daily_review_snapshot` and `bulk_update_tasks` tools. Update server `instructions=` string. |
| `src/ultimate_brain_mcp/formatters.py` | Extend `format_task` signature with optional `project_lookup` and `tag_lookup`. Populate `project_name` / `area_tag_names` when lookups are present. |
| `src/ultimate_brain_mcp/notion_client.py` | No changes — `query_data_source`, `update_page`, `get_data_source` already cover everything. |
| `tests/test_tools.py` | New live tests: `daily_review_snapshot` shape + time fields, `bulk_update_tasks` per-row success + per-row failure (deliberate bad id), `update_task` with `location`, `search_tasks(due_on=...)`. |
| `tests/test_formatters.py` | Unit test: `format_task` with and without lookups. |
| `tests/conftest.py` | Extend `seed_tasks` fixture to include a task with a project + label so the snapshot test has real lookup data. |
| `.env.example` | Document `UB_TIMEZONE`. |
| `README.md` | Bump tool count 28 → 30; document new tools and the timezone env var. |
| `CLAUDE.md` (project) | Update tool-count and Architecture sections that reference 28 tools. |

## Existing utilities to reuse

| Utility | Path | Why |
|---------|------|-----|
| `_discover_note_types()` | `src/ultimate_brain_mcp/server.py:78` | Pattern for lifespan-time schema discovery with stderr fallback — copy for tasks-schema. |
| `extract_select_options()` | `src/ultimate_brain_mcp/config.py:38` | Reuse for `location_options` + `labels_options` extraction. New `extract_property_metadata()` builds on it. |
| `NotionClient.get_data_source()` | `src/ultimate_brain_mcp/notion_client.py:537` | Already used for note-types discovery — reuse for tasks data-source schema. |
| `_bounded_gather()` | `src/ultimate_brain_mcp/server.py:175` | Concurrency cap pattern for `bulk_update_tasks` — same shape as `set_page_content` block deletes. |
| `_handle_api_error()` | `src/ultimate_brain_mcp/server.py:154` | Per-row error formatting in `bulk_update_tasks` results. |
| `asyncio.gather` parallel pattern | `src/ultimate_brain_mcp/server.py:1349` (`daily_summary`) | Direct precedent for `daily_review_snapshot`'s parallel queries. |
| `format_task()`, `format_project()`, `format_tag()` | `src/ultimate_brain_mcp/formatters.py` | Per-task formatting reused inside `daily_review_snapshot`. |
| Property builders (`_prop_*`) | `src/ultimate_brain_mcp/server.py:193` onward | Reuse for `location` payload construction across the three select/multi_select/status types. |

## Implementation order

Each step builds on the prior — implement in sequence, test after each.

1. **Config + timezone** — `UBConfig.timezone` field, `UB_TIMEZONE` env var, `zoneinfo` validation in `from_env()`.
2. **Schema introspection** — `extract_property_metadata()` helper in `config.py`; `_discover_tasks_schema()` + `TasksSchema` dataclass on `AppContext` in `server.py` lifespan.
3. **Formatter resolved names** — extend `format_task` signature; existing call sites unchanged.
4. **`update_task` / `create_task`** — add `location` and `tag_ids` parameters; consult `app.tasks_schema` for the right Notion payload shape.
5. **`search_tasks`** — add `due_on` parameter with mutual-exclusion guard against `due_before`/`due_after`.
6. **`daily_review_snapshot`** — register tool; assemble buckets via parallel `asyncio.gather`; build lookups; compute outstanding union; format every task with lookups; return shape.
7. **`bulk_update_tasks`** — register tool; per-row try/except producing `{ok, error?}` results; concurrency-capped via `_bounded_gather`.
8. **Tests** — formatter unit tests, then live MCP tests for each new behaviour.
9. **Docs** — `.env.example`, `README.md`, project `CLAUDE.md` tool-count and tool descriptions.

## Verification

- `uv run pytest tests/` — all existing tests pass plus the new ones.
- `uv run mcp dev src/ultimate_brain_mcp/server.py` — interactive inspector: call `daily_review_snapshot()` and confirm `now`, `timezone`, all five buckets, `outstanding`, `lookups`, `task_schema` are populated; call `bulk_update_tasks` with two valid + one bogus task_id and confirm per-row results.
- End-to-end against the skill: re-run the `ub daily-review` workflow against a live workspace and verify it completes the data-gathering and apply phases in exactly two MCP calls. Inspect that completed tasks have `My Day=false`, accepted tasks have `My Day=true` + correct `Due`, and triaged inbox tasks have project/area/location/due set.
- Manual inspection: tail the server stderr at startup to confirm tasks-schema discovery succeeded (or fell back cleanly).

## Tool-description quality checklist (applied per `ai_docs/Custom_MCP_Servers.md`)

Every new and modified tool docstring will satisfy:

- [ ] **Front-loaded action verb** — first words say what the tool does (`Get…`, `Apply…`, `Set…`).
- [ ] **Sibling-tool disambiguation** — explicit "use X instead when…" clauses (snapshot ↔ daily_summary, bulk_update_tasks ↔ update_task, location-via-Labels ↔ location-via-property).
- [ ] **Return-shape description** — every field listed with type/example.
- [ ] **`Literal` types** — for status, priority, location_property_type, response_format-style enums.
- [ ] **Actionable errors** — say what went wrong, what to fix, where to look (`Use search_tasks to find valid task IDs`).
- [ ] **Date-format examples** — `'2026-05-09'` not just `'YYYY-MM-DD'`.
- [ ] **`ToolAnnotations`** — readOnlyHint / destructiveHint / idempotentHint set correctly on every new tool.
- [ ] **Hidden infrastructure** — no DS IDs, secrets, or Notion property names leak into tool parameters; everything flows through `AppContext`.
- [ ] **Server `instructions=` updated** — point agents at the new workflow tools when appropriate.

## Risks and mitigations

- **Snapshot payload size on large workspaces.** Mitigate with bucket-level caps (`inbox_limit` exposed; other buckets default 100) and a per-bucket `truncated: true` flag when the cap is hit. The agent can re-query a specific bucket via the existing tools if needed.
- **Tasks-schema discovery failure.** Fallback to empty options + `has_location_property: false`; the skill already handles missing-location-convention via its `AskUserQuestion` discovery prompt.
- **Bulk update partial failures.** Returned per-row, never raises. Agent surfaces failed rows through the existing skill `Tool error` retry/skip/abort gate.
- **`update_task` semantics drift.** New `location` and `tag_ids` parameters are optional and default to `None`; omitted means no-op for that field, identical to today's behaviour.
- **Backwards compatibility.** Every change is additive — no breaking changes to existing 28 tools, no changes to existing test assertions. New tests cover only new surfaces.
- **Timezone resolution edge cases.** `UB_TIMEZONE` validation at startup catches typos before any tool call. System-tz fallback uses Python's default `zoneinfo` resolution which honours `TZ` env var on Unix.
- **Tool-count creep (28 → 30).** Mitigated by `instructions=` steering and by the fact that the new tools collapse N-call workflows into 1. A future PR may consolidate `get_my_day` / `get_inbox_tasks` into the snapshot if usage shows they're never called outside it.
