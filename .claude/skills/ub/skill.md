---
name: ub
description: Workflows for Thomas Frank's Ultimate Brain via the ultimate-brain-mcp server. A multi-function dispatcher — invoke with an argument naming the function. Available functions — daily-review (review today's completed and outstanding tasks, triage inbox, plan tomorrow's My Day flag with confirmation gates). USE WHEN ub, ultimate brain, daily review, plan my day, triage inbox, ub daily-review, daily plan, end of day review, morning planning.
---

# Ultimate Brain Skill

Multi-function workflow harness for Thomas Frank's Ultimate Brain. Every function in this skill operates against the `ultimate-brain-mcp` MCP server (tools prefixed `mcp__ultimate-brain__*`).

## How to invoke

The skill takes a single argument naming the function. Read ONLY the matching reference file before acting — do not load other reference files.

| Argument | Reference file | Purpose |
|----------|----------------|---------|
| `daily-review` | `references/daily-review.md` | Review today's completed and outstanding tasks, triage the inbox, and propose the next-day My Day batch. Exactly two MCP calls per run — `daily_review_snapshot` for everything-as-data, `bulk_update_tasks` for everything-as-writes. Three confirmation gates between them. |

If the user names an argument that is not in the table above, stop and list the available functions.

## Tool prerequisites

- The `ultimate-brain-mcp` server must be connected. If `mcp__ultimate-brain__*` tools are not visible in the current session, stop immediately and tell the user the server is not connected.
- Today's local date is computed in `Europe/London`. When a date is needed, run `date -u +%Y-%m-%dT%H:%M:%SZ` and convert, or use the conversation's stated current date.

## Common conventions (apply to every function)

- Always present proposed changes BEFORE writing — every function has at least one explicit confirmation gate.
- Never archive, complete, or delete tasks during a review unless the user explicitly approves each one.
- Treat tool annotations literally: `archive_item` and `set_page_content` are flagged destructive; do not call them without confirmation.
- Surface every MCP error verbatim and ask retry/skip/abort — do not silently swallow.
- All proposals are presented as compact tables, not prose paragraphs.
- IDs are opaque — never show full Notion page IDs to the user; show task names and resolve IDs internally.

## Adding new functions later

1. Add a new row to the dispatch table above.
2. Create `references/<argument>.md` with the full instructions for that function.
3. Keep each reference file self-contained — it should be readable without the others.
