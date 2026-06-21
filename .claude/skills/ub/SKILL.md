---
name: ub
description: >
  Ultimate Brain (UB) is the authoritative system of record for goals, projects, tasks, and notes (Journal, Meeting, Idea). Trigger proactively on conversational signals, not assumed knowledge.
  Goals/projects/tasks: trigger when the user mentions work they are doing or planning; expresses intent (I need to, I should, I want to); asks about priorities or what is next; runs a review; or uses words like task, project, goal, backlog, follow-up, capture, track.
  Notes: (1) IDEAS - proactively capture genuinely new, developed project ideas, but do NOT treat ordinary questions, how-to or research requests, or passing musings as ideas, and check it is not already an Idea, Project, or Goal first. (2) JOURNAL - when the user refers to their journal or asks Claude to review or add feedback; Claude reads and appends feedback on request but never authors entries. (3) MEETING - read access only for context.
  Do not wait to be asked - check UB first and surface what is relevant. UB is the single source of truth.
---

# Ultimate Brain Skill

## Purpose

Ultimate Brain (UB) is the **authoritative system of record** for goals, projects, tasks, and notes. This skill governs how to interact with it via the available UB MCP tools. UB is the default place where information is stored and retrieved - prefer it over chat-only answers or loose files when capturing anything that belongs in the user's system.

UB sits in a hierarchy:
- **Goals** - desired outcomes, each with status (Active / Achieved / Dropped) and optional deadline
- **Projects** - time-bounded bodies of work linked to goals, with status (Not Started / Doing / Ongoing / Done)
- **Tasks** - individual actions linked to projects, with status, priority, due date, and My Day flag
- **Notes** - free-form pages with a `note_type` field. This skill governs three of those types: **Journal**, **Meeting**, and **Idea**. (Other types such as Reference, Web Clip, Plan, and Brainstorm exist but are out of scope here.)

---

## When This Skill Applies

Trigger this skill whenever **any** signal below is present - derived from the live conversation, not assumed from prior knowledge. **Do not rely on memory to decide whether to trigger.** When in doubt, check UB - a quick search is cheap and missing relevant context is not.

### Goal / Project / Task signals
- The user mentions something they are working on, planning, or trying to achieve
- The user expresses intent: "I need to...", "I should...", "I want to...", "I'm trying to..."
- The user references something that sounds like a project or goal
- The user asks about priorities, workload, what's next, or what's on their plate
- Any daily, weekly, or periodic review is underway
- The words task, to-do, project, goal, backlog, follow-up, capture, or track appear
- An action item, decision, or next step surfaces naturally
- The user asks whether something is already captured, or says something is done

### Note signals
- **Idea** - the user is shaping, proposing, or developing a concept for a possible project (see the Ideas section for the novelty gate that keeps ordinary questions from becoming ideas)
- **Journal** - the user refers to their journal, or asks Claude to review, discuss, or add feedback to a journal entry
- **Meeting** - meeting notes, a transcript, or a summary would give useful context for the current discussion

**Do not wait to be asked.** Check UB, surface what's relevant, and offer to make changes - subject to the per-type write rules below.

---

## Lookup Patterns

### Quick orientation (start of day / general status check)
```
daily_summary -> overview counts
```

### Full daily review
```
daily_review_snapshot -> all buckets: overdue, due today, My Day, inbox, projects lookup
```

### Topic-based lookup (mid-conversation)
When a topic surfaces, run parallel searches across goals, projects, tasks, and (where relevant) notes:
```
search_goals(query="<topic keywords>")
search_projects(query="<topic keywords>")
search_tasks(query="<topic keywords>")
search_notes(query="<topic keywords>", note_type="<Idea|Meeting|Journal>")
```
Use keywords from what the user actually said. If a matching project is found, call `get_project_detail(project_id)`; if a matching goal is found, call `get_goal_detail(goal_id)`; if a matching note is found, call `get_note_content(note_id)` for its body.

**Important search limitation:** `search_notes(query=...)` matches **note titles only**, not body text. When titles are unlikely to contain the keyword, filter by `note_type` (and `date_after` if relevant) and scan the results rather than relying on the query string alone.

### Inbox triage
```
get_inbox_tasks -> unprocessed tasks (no project, no due date, status = To Do)
```

---

## Notes

Notes are the default store for the user's Journal, Meeting, and Idea content. Each type has a distinct posture - the write rules differ, so treat them separately.

### Idea (proactive capture)

This is the one note type Claude captures proactively. The user often talks through ideas for projects with Claude, and wants the good ones captured in UB so they live in one place and can be developed over time.

**Novelty gate - decide whether something is actually an idea before doing anything.**

Treat as an idea worth capturing when the user is *shaping a concept they might pursue*:
- Proposing or describing something they could build, make, set up, or start ("I've been thinking about...", "what if we built...", "idea:", "I want to create...")
- Giving it substance - a problem it solves, an approach, components - especially when elaborated over several turns
- Returning to or expanding a concept raised earlier

Do **not** treat as an idea (do not capture, do not even propose):
- Factual or how-to questions, research requests, or troubleshooting
- Asking Claude's opinion on something external to them
- Casual musing or thinking aloud with no intent to pursue it
- Work that is plainly already a tracked project or task

When unsure whether something even qualifies as an idea, lean towards *not* capturing - over-capturing erodes trust in the Idea store.

**Before creating anything, run a dedup / novelty check:**
1. `search_notes(note_type="Idea", query="<title keywords>")` - look for an existing idea note
2. When keywords are unlikely to appear in a title, also scan recent ideas: `search_notes(note_type="Idea")` (optionally with `date_after`)
3. `search_projects(query="<keywords>")` and `search_goals(query="<keywords>")` - it may already be in flight as a project or goal

Then:
- **Existing Idea note found** -> don't duplicate. Extend it instead (see "develop in one place" below).
- **Existing Project or Goal found** -> surface it and ask whether to add to that, rather than creating a new idea.
- **Genuinely new** -> capture it.

**How to capture (per the user's chosen behaviour):**
- **Clearly developed** (a nameable concept with a problem/purpose and some substance, often built up over the conversation) -> create the note automatically, then tell the user you've done it and where.
- **Still nascent** (a one-line spark, ambiguous, or you're not certain it's novel) -> propose it first and create only on confirmation.

**Creating an Idea note:**
```
create_note(
  name="<clear, specific title>",
  note_type="Idea",
  content="<structured markdown capture>"
)
```
Structure the body so it's useful later, e.g. a short concept summary, the problem or why, the approach or components, open questions, and possible next steps. Link `project_id` only if the idea genuinely belongs to an existing project (usually it won't).

**Develop in one place (extending an existing idea):** when an idea grows, add to its existing note rather than scattering it. Append a dated section so the development history reads as a log:
```
set_page_content(page_id="<idea note id>", mode="append",
  content="\n---\n## Update - <YYYY-MM-DD>\n<new thinking>")
```
Use `mode="append"` so existing content and formatting are preserved.

### Journal (read and feedback only - never authored by Claude)

The user writes their own journal entries. Claude does **not** create Journal notes. Claude's role is:
- **Read** - retrieve and review entries when the user wants to discuss them: `search_notes(note_type="Journal", ...)` then `get_note_content(note_id)`.
- **Add feedback on request** - when the user asks Claude to add its thoughts, append a section to the **bottom** of that entry under the heading **"Claude's Feedback"**:
```
set_page_content(page_id="<journal note id>", mode="append",
  content="\n---\n## Claude's Feedback - <YYYY-MM-DD>\n<feedback>")
```
An explicit request to add feedback is the go-ahead, so you can write it - but show the feedback in chat as you add it. Never edit or overwrite the user's own journal text, and never create a new Journal entry on their behalf.

### Meeting (read access only)

Meeting notes, transcripts, and summaries are captured elsewhere and synced into UB for reference. Claude **reads** them for context and does **not** author or edit them.
- Pull for context: `search_notes(note_type="Meeting", query="<keywords>", date_after="<YYYY-MM-DD>")` then `get_note_content(note_id)`.
- If the user wants a summary of a meeting, provide it in chat (or wherever they direct); do not create or modify a Meeting note unless explicitly told to.

---

## Write Operations

Always **confirm with the user before writing**, unless they have explicitly asked you to go ahead, with these per-type exceptions for notes:
- **Idea, clearly developed** -> create automatically, then report (no pre-confirmation needed).
- **Idea, nascent** -> propose first, create on confirmation.
- **Journal feedback** -> an explicit request to add feedback is itself the go-ahead.
- **Meeting / Journal entries** -> never authored by Claude.

Batch updates of 3+ tasks should use `bulk_update_tasks`.

| Operation | Tool |
|---|---|
| Add a task | `create_task` |
| Add a project | `create_project` |
| Add a goal | `create_goal` |
| Add an idea note | `create_note` (note_type="Idea") |
| Extend an idea / append journal feedback | `set_page_content` (mode="append") |
| Update a task | `update_task` or `bulk_update_tasks` |
| Update a project | `update_project` |
| Update a goal | `update_goal` |
| Update note properties (title, type, links) | `update_note` |
| Read a note's body | `get_note_content` |
| Mark task done | `complete_task` |
| Archive/remove | `archive_item` |

When creating items, link them correctly:
- Tasks -> link to a `project_id` where possible
- Projects -> link to a `goal_id` where possible
- Ideas -> link to a `project_id` only if they genuinely belong to one
- Set `due` and `priority` on tasks if context makes them clear

Note: `update_note` changes a note's *properties* (title, type, project link, tags) only. To change a note's *body*, use `set_page_content`.

---

## Surfacing Relevance Mid-Conversation

When UB data is relevant, present it concisely - don't dump everything. Pattern:

> **UB context:** [Project / Goal / Note name] - [status or type] - [key detail]
> Relevant items: [2-3 most pertinent]
> Want me to [specific proposed action]?

Only show genuinely relevant items. If nothing matches, say so briefly and move on.

---

## Proactive Suggestions

After surfacing UB data, look for opportunities to:
- Flag overdue tasks in the relevant project
- Suggest breaking a vague goal into a concrete project
- Recommend promoting a task to a project if scope has grown
- Suggest adding a due date or priority if missing
- Propose linking an orphan task to the right project
- Notice if a project has no open tasks (may be stalled or complete)
- Offer to capture an action item from the current conversation
- Offer to capture a developed idea (subject to the novelty gate), or extend an existing idea the discussion has moved forward
- Spot when an idea has matured enough to become a project, and offer to promote it

---

## Key Rules

1. **UB is authoritative** - never assume a goal/project/task/note exists or doesn't without checking. Memory is a hint, not the source of truth.
2. **Search before writing** - find the relevant item IDs before updating, linking, or deduplicating.
3. **Confirm before mutating** - present proposed changes and wait for approval, except for the note exceptions listed under Write Operations.
4. **Respect the note postures** - Claude captures Ideas proactively (with the novelty and dedup gates), reads and adds feedback to Journals but never authors them, and only ever reads Meeting notes.
5. **Don't over-capture ideas** - an ordinary question is not an idea. When unsure, don't capture.
6. **Keep ideas in one place** - extend an existing idea note rather than creating duplicates.
7. **Keep items well-linked** - tasks to projects, projects to goals, ideas to projects where they belong. Flag orphans when spotted.
8. **Be concise** - surface the signal, not the noise.

---

## Future Extensions

This skill is designed to support targeted function invocation (e.g. `ub:daily-review`, `ub:inbox`, `ub:goals`, `ub:ideas`). For now, apply the full contextual approach above.
