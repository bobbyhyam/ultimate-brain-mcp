# Function: daily-review

End-of-day or start-of-day review for Bobby's task list. **Exactly two MCP tool calls per run**: `daily_review_snapshot()` at the start (covers all data + time + schema), `bulk_update_tasks(updates=[...])` at the end (covers all writes). Three confirmation gates separate the read from the write.

All tool names below are MCP tools provided by `ultimate-brain-mcp`. Use the `mcp__ultimate-brain__<tool>` form when calling them.

## User-prompt convention (applies to every gate in this workflow)

If the `AskUserQuestion` tool is available in the current session, **use it for every user-facing question in this workflow** — confirmations, per-task decisions, location-convention discovery, batch confirmations, retry-vs-abort prompts, etc. Do not fall back to plain-text prompts when `AskUserQuestion` is available.

When `AskUserQuestion` is NOT available, use plain-text prompts as a fallback (the wording shown inline below).

`AskUserQuestion` constraints to respect:

- 1–4 questions per call; 2–4 options per question.
- An "Other" option is added automatically — never include one yourself.
- `multiSelect` only when the answers are genuinely non-exclusive.
- Use a short `header` (≤12 chars) per question.

## Phase 1 — Snapshot (one tool call)

Make exactly ONE tool call: `daily_review_snapshot()` (no arguments unless you need to widen `inbox_limit` beyond the 100 default).

That single call returns everything subsequent phases need:

- `now` — ISO8601 with offset (workspace-tz authoritative).
- `timezone` — IANA name (e.g. `Europe/London`).
- `buckets` — `completed_today`, `overdue_or_due_today`, `due_tomorrow`, `on_my_day`, `inbox`. Each task already has `project_name` and `area_tag_names` resolved — never look up IDs manually.
- `outstanding` — pre-deduplicated union of `overdue_or_due_today` ∪ `on_my_day`, in priority/due order. **This is the outstanding set for the rest of the workflow** — do not recompute it.
- `lookups.projects` and `lookups.area_tags` — `{id → {name, ...}}`. Reference these when proposing project/tag assignments for inbox triage.
- `task_schema` — drives Phase 4a's location-convention decision. No `get_page` needed.
- `truncated` — `{bucket_name → bool}`. If any flag is true, surface it to the user before proceeding so they can decide whether to widen `inbox_limit` or accept the cap.

If the snapshot returns an `error` field, abort the run and surface the error verbatim.

## Phase 2 — Establish the target day

Derive from `snapshot.now` parsed in `snapshot.timezone`:

- Hour ≥ 16:00 → evening run; **target day = tomorrow** (next calendar day).
- Hour < 16:00 → morning run; **target day = today**.

Confirm with the user via `AskUserQuestion`:

- **question**: `"Detected {evening|morning} run — plan My Day for {target_date}?"`
- **header**: `Target day`
- **options**:
  - `"Use {target_date} ({weekday})"` — accepts the auto-detection (Recommended).
  - `"Use today instead"` — overrides to today.
  - `"Use tomorrow instead"` — overrides to tomorrow.
  - `"Cancel review"` — abort.

Allow "Other" for a free-form date override (`YYYY-MM-DD`).

Fallback (no `AskUserQuestion`):
> "Detected an evening run — planning My Day for tomorrow ({YYYY-MM-DD}). Reply `ok`, or `morning` to plan for today, or give a date to override."

Lock `target_date` (YYYY-MM-DD) before continuing. Compute `today` and `tomorrow` from `snapshot.now` for downstream use.

## Phase 3 — Restate today, ask about pushes

Print TWO compact tables, in this order:

**Completed today** — from `snapshot.buckets.completed_today`. Columns: Name | Project (use `project_name`, already resolved) | Priority.

**Outstanding** — from `snapshot.outstanding` (already deduplicated and sorted). Columns: Name | Due | My Day | Project | Priority. The snapshot returns these in workable order; only re-sort if the user pushes back on the ordering.

### Per-task decision loop

For each outstanding task, in the sorted order, ask **one `AskUserQuestion` per task** with the same four options every time:

- **question**: `"Outstanding {i}/{N}: '{task name}' (due {due_or_None}, My Day={true/false}, project {project_name_or_None}). Keep on today or push to {target_date}?"`
- **header**: `Task {i}/{N}`
- **options**:
  1. `"Keep on today"` — leave due date alone, leave My Day as-is.
  2. `"Push to {target_date}"` — set due = `target_date`, include in tomorrow's My Day batch.
  3. `"Push ALL remaining"` — apply `push` to this task AND every still-unanswered task; stop asking.
  4. `"Keep ALL remaining"` — apply `keep_today` to this task AND every still-unanswered task; stop asking.

   Allow "Other" for free-form input like `"skip"` or `"defer to 2026-05-15"`.

Always include all four options on every per-task question — even on the last task, even after a previous answer was a single-task choice. Do NOT batch multiple per-task questions into one `AskUserQuestion` call: the "ALL remaining" semantics require sequential evaluation.

Stopping rule for the loop:

- If the answer is `"Push ALL remaining"` or `"Keep ALL remaining"`: apply that decision to the current task AND every remaining unanswered task, then exit the loop immediately. Do not issue further questions for that batch.
- If the answer is `"Other"` and the free-form text matches `skip`, record `skip`. If it matches a date (`YYYY-MM-DD`), treat as `push` with that explicit date instead of `target_date`. Anything else: re-ask the same task with the original four options plus the user's text echoed back for clarification.

Fallback (no `AskUserQuestion`):

> "For each outstanding task, do you want to keep working on it today or push to {target_date}? Reply task-by-task (e.g. `1 push, 2 keep, 3 skip`), or `push all` / `keep all` for the whole list."

Recorded per-task decisions:

| Decision | Meaning |
|----------|---------|
| `keep_today` | Leave due date alone; leave My Day as-is. |
| `push` | Will set due = `target_date` (or the explicit date the user typed via "Other") and include in the My Day batch for `target_date`. |
| `skip` | Drop from the My Day plan (not relevant tomorrow). |

If the user is doing a morning run, `keep_today` and `target_date == today` are equivalent — task stays as-is.

## Phase 4 — Triage the inbox

Goal: every inbox task gains a project (or Area tag), a location, and a future due date — unless the user explicitly says "no due date".

### 4a. Determine the location convention from `snapshot.task_schema`

Read `snapshot.task_schema` directly — no `get_page` call needed. Three cases:

1. **`has_location_property == true`** → use the dedicated property. Constrain proposed values to `task_schema.location_options` (server validates them). Pass the chosen value via the `location` parameter on `bulk_update_tasks`.
2. **`has_location_property == false`** AND `task_schema.labels_options` contains entries that look like locations (e.g. `@home`, `@office`, `@errands`) → location lives in `Labels`. Pass the chosen value(s) via the `labels` parameter (replaces the existing list — preserve any non-location labels you saw on the task).
3. **Neither applies** → skip the location field for the run. Tell the user once: "This workspace has no Location property and no @-style labels — locations will be left blank."

If the situation is genuinely ambiguous (e.g. labels include both `@home` and `deep-work`-style entries), confirm via `AskUserQuestion`:

- **question**: `"How are locations stored on tasks in this workspace?"`
- **header**: `Location conv.`
- **options**:
  - `"Dedicated Location property"` — if `task_schema.has_location_property` is true.
  - `"Labels with @ prefix"` — when @-style entries appear in `labels_options`.
  - `"Don't track locations — skip this field"`.

Allow "Other" for any other scheme. Cache the answer for the rest of this run.

Fallback (no `AskUserQuestion`):
> "How are locations stored on tasks here — a property, a label like `@home`, or something else?"

### 4b. Propose triage for each inbox task

For each inbox task in `snapshot.buckets.inbox`, infer:

- **Project**: best match from `snapshot.lookups.projects` based on the task name. If nothing fits, propose Area tag instead.
- **Area tag**: only if no project fits — pick the closest entry from `snapshot.lookups.area_tags`.
- **Location**: infer from the task name (e.g. "buy milk" → `@errands`, "deploy to prod" → `@office`, "fix lawnmower" → `@home`, "call dentist" → `@phone` or `@errands`). Constrain proposals to the valid set: `task_schema.location_options` (Case 1) or the @-prefixed entries of `task_schema.labels_options` (Case 2). If genuinely unclear, mark `?` and ask.
- **Due date**: pick a reasonable future date based on apparent urgency:
  - "today / asap / urgent" wording → today
  - "tomorrow / first thing" wording → tomorrow
  - generic action → 7 days from today
  - low-priority / someday → 30 days from today
  Never propose a past date. Never leave empty unless the user says "no due date for this one".

Print a single proposal table:

| # | Task | Project / Area | Location | Due | Notes |

Then ask via `AskUserQuestion`:

- **question**: `"Inbox triage proposal — accept all, edit specific rows, or skip the inbox?"`
- **header**: `Inbox triage`
- **options**:
  - `"Accept all"` (Recommended) — apply every proposed row as shown.
  - `"Edit specific rows"` — user will list edits via "Other" (e.g. `2 project=Garden, due=2026-05-12; 4 location=@home`).
  - `"Mark all 'no due date'"` — apply project / area / location but leave Due empty on every triaged task.
  - `"Skip inbox triage this run"` — make no changes to inbox tasks.

If the user picks `"Edit specific rows"` or types via "Other", parse the edits and re-display the updated table, then ask again with the same options until the user picks `"Accept all"` or one of the other terminal choices. Do NOT write to Notion yet.

Fallback (no `AskUserQuestion`):
> "Edit any rows (e.g. `2 project=Garden, due=2026-05-12`), or reply `ok` to accept all."

## Phase 5 — Propose the My Day batch for `target_date`

Compute the candidate set:

- Every outstanding task marked `keep_today` (when `target_date == today`) or `push` (any run).
- Every `due tomorrow` task when `target_date == tomorrow` (auto-included).
- Every triaged inbox task whose proposed due date falls on or before `target_date`.
- Any task the user names explicitly during conversation.

Sort: priority (High → Medium → Low → none), then due ascending, then name.

Print the candidate batch as a table:

| # | Task | Current Due | Proposed Due | Project | Priority |

Ask via `AskUserQuestion`:

- **question**: `"Proposed My Day for {target_date} — {N} tasks. Apply, edit, or cancel?"`
- **header**: `My Day batch`
- **options**:
  - `"Apply as shown"` (Recommended).
  - `"Edit the batch"` — user will provide edits via "Other" (e.g. `remove 3; add 'pay invoice'; due 5 → 2026-05-13`).
  - `"Drop everything except High priority"` — keep only High-priority rows, discard the rest.
  - `"Cancel review"` — abort with no writes.

If the user picks `"Edit the batch"` or types via "Other", apply the edits to the in-memory plan, re-display the updated table, then ask again with the same options. Loop until the user picks `"Apply as shown"`, `"Drop everything except High priority"`, or `"Cancel review"`.

Fallback (no `AskUserQuestion`):
> "This is the proposed My Day for {target_date}. Reply `ok`, or edit (`add <task name>`, `remove 3`, `swap due of 5 to 2026-05-13`, etc.)."

## Phase 6 — Apply changes (one tool call)

ONLY after every confirmation gate has passed. Build a single `bulk_update_tasks(updates=[...])` call covering inbox triage + defensive My Day clears + the My Day batch.

### 6a. Construct the `updates` list

Walk three sources, in this order, and append one update entry per task. Each entry follows the `BulkTaskUpdate` shape:

```jsonc
{
  "task_id": "<id>",
  "project_id": "<id>",          // optional
  "tag_ids": ["<id>", ...],      // optional
  "labels": ["@home", ...],       // optional — replaces existing
  "location": "@home",            // optional — only if task_schema.has_location_property
  "due": "2026-05-09",            // optional — YYYY-MM-DD
  "my_day": true                  // optional
}
```

**Source 1 — Inbox triage** (one entry per triaged inbox task accepted in Phase 4):

- `project_id` — chosen project ID, OR omit and use `tag_ids: [<area_tag_id>]` instead.
- Location: pick the right field per the Phase 4a rule:
  - Case 1 (dedicated property) → `location: "<value>"`.
  - Case 2 (Labels-based) → `labels: [...existing_labels, "<value>"]` (preserve non-location labels seen on the task in `snapshot.buckets.inbox`).
  - Case 3 (neither) → omit.
- `due` — proposed due date from Phase 4b; omit if the user said "no due date for this one".

**Source 2 — Defensive clear of My Day on completed-today tasks** (rare; usually empty):

- For any task in `snapshot.buckets.completed_today` whose `my_day == true`, append `{task_id, my_day: false}`. Skip entirely if the list is empty — saves nothing to `updates`.

**Source 3 — My Day batch for `target_date`** (from the confirmed Phase 5 list):

- For each accepted task, append `{task_id, my_day: true, due: "<target_date>"}`.
- Exceptions to honour while building the entry:
  - If the user marked `keep_today` AND `target_date == today` AND the task's current `due` already equals `target_date`, omit the `due` field — set `my_day` only.
  - If the user said `keep` on an existing due date during Phase 5 edits, omit the `due` field.
  - If a task's existing due is later than `target_date` and the user said `push`, set `due: <target_date>` (intent of `push` is to pull it forward).

### 6b. Make the single call

Issue ONE call:

```
bulk_update_tasks(updates=<the assembled list>)
```

### 6c. Surface the results

Read the response:

- `summary` — `{ok, failed, total}`. If `failed == 0`, proceed straight to the confirmation summary.
- `results[]` — per-row outcomes. Walk in order:
  - Successful rows (`ok: true`) — fold into the confirmation table.
  - Failed rows (`ok: false`) — collect into a separate "errors" list with the `error` message.
  - Rows with `_warnings` — print the warning under the task's row in the confirmation table (typically the location-ignored warning when `task_schema.has_location_property` was false; only happens if we mis-routed).

If any rows failed, ask via `AskUserQuestion`:

- **question**: `"{N} of {total} updates failed. {first failure summary}. How do you want to handle it?"`
- **header**: `Bulk failures`
- **options**:
  - `"Retry the failed rows"` — call `bulk_update_tasks` again with only the failed entries.
  - `"Skip the failed rows and continue"`.
  - `"Abort and roll back to plan"` — surface the error list, do not undo successful rows automatically (the underlying writes are idempotent and partial state is acceptable here).

### 6d. Confirmation summary

Print a final table:

| Task | Action | New Due | My Day |

Followed by one short line: "Daily review complete — N tasks triaged, M tasks on My Day for {target_date}."

## Stop conditions

- If at any confirmation gate the user picks `"Cancel review"` (or replies `stop` / `cancel` / `abort` in the fallback path), write nothing further and end the run with a one-line acknowledgement.
- If a tool returns an error, surface the error verbatim and ask via `AskUserQuestion`:
  - **question**: `"Tool {tool_name} failed on '{task name}': {error}. How do you want to handle it?"`
  - **header**: `Tool error`
  - **options**:
    - `"Retry the same call"`.
    - `"Skip this task and continue"`.
    - `"Abort the entire run"`.
  Fallback (no `AskUserQuestion`): "retry, skip this task, or abort the run."
- Never proceed to a write phase without an explicit acceptance choice (`Apply as shown`, `Accept all`, etc.) from the user on the immediately preceding gate.

## Notes for the executor

- **A successful run is exactly TWO MCP tool calls**: `daily_review_snapshot` at the start and `bulk_update_tasks` at the end. Anything else (multiple `search_tasks`, `update_task` per row, `get_page` for schema discovery) is a regression — surface it and reconsider.
- All date math uses `snapshot.now` parsed against `snapshot.timezone`. Do not shell out for `date` — the snapshot is authoritative.
- `bulk_update_tasks` is idempotent and never raises on a single failure — failures come back per-row with `ok: false` and a human-readable `error`.
- `complete_task` is intentionally NOT used in this workflow; completing tasks is the user's job, not the review's.
- The snapshot returns tasks with `project_name` and `area_tag_names` already resolved — never look up IDs manually. Project IDs and tag IDs ARE needed for the write call (`bulk_update_tasks`), but the names are for display only.
- `task_schema.has_location_property` is the single source of truth for where locations live. Trust it over heuristics.
- If `snapshot.truncated[<bucket>]` is true on a bucket the user cares about, surface the cap and offer to re-run the snapshot with a wider `inbox_limit` (other bucket caps are not user-overridable in this workflow).
