"""Environment variable loading, constants, and data source ID registry."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# ---------------------------------------------------------------------------
# Status / type literals used across tools
# ---------------------------------------------------------------------------

TASK_STATUSES = ["To Do", "Doing", "Done"]
PROJECT_STATUSES = ["Not Started", "Doing", "Ongoing", "Done"]
GOAL_STATUSES = ["Active", "Achieved", "Dropped"]
TAG_TYPES = ["Area", "Resource", "Entity"]

# Notes Type select. Ultimate Brain v3.0 ships 13 options. Used as a fallback
# when live discovery from the Notion data source schema fails at server
# startup; the live list is preferred and lives on AppContext.note_types.
NOTE_TYPES = [
    "Journal", "Meeting", "Web Clip", "Lecture", "Reference",
    "Book", "Idea", "Plan", "Recipe", "Voice Note",
    "Daily", "Note", "Brainstorm",
]

# Property name on the Notes data source that holds the Type select.
# Single source of truth — used by lifespan discovery and by formatters.
NOTES_TYPE_PROP = "Type"

TASK_PRIORITIES = ["Low", "Medium", "High"]


# ---------------------------------------------------------------------------
# Schema introspection helpers
# ---------------------------------------------------------------------------


def extract_select_options(schema: dict, prop_name: str) -> list[str]:
    """Pull select option names for *prop_name* out of a Notion data source schema.

    Returns an empty list if the property is missing or isn't a select — the
    caller is expected to treat empty as a discovery failure.
    """
    prop = schema.get("properties", {}).get(prop_name)
    if not isinstance(prop, dict):
        return []
    if prop.get("type") != "select":
        return []
    options = prop.get("select", {}).get("options", [])
    return [opt["name"] for opt in options if isinstance(opt, dict) and "name" in opt]


def extract_property_metadata(schema: dict, prop_name: str) -> dict:
    """Return shape + options for *prop_name* on a Notion data source schema.

    Used for live discovery of Tasks Location and Labels properties at server
    startup. Handles select / multi_select / status uniformly — they all carry
    options under their type-specific key.

    Returns ``{"exists": False}`` if the property is absent. Otherwise:
    ``{"exists": True, "name": <prop_name>, "type": <ptype>, "options": [<str>, ...]}``.
    Options is empty for property types that don't carry a fixed option list.
    """
    prop = schema.get("properties", {}).get(prop_name)
    if not isinstance(prop, dict):
        return {"exists": False}
    ptype = prop.get("type")
    options: list[str] = []
    if ptype in ("select", "multi_select", "status"):
        raw_options = prop.get(ptype, {}).get("options", [])
        options = [
            opt["name"]
            for opt in raw_options
            if isinstance(opt, dict) and "name" in opt
        ]
    return {"exists": True, "name": prop_name, "type": ptype, "options": options}

# ---------------------------------------------------------------------------
# Secondary database registry — maps friendly name → env var
# ---------------------------------------------------------------------------

SECONDARY_DB_ENV_MAP: dict[str, str] = {
    "Work Sessions": "UB_WORK_SESSIONS_DS_ID",
    "Milestones": "UB_MILESTONES_DS_ID",
    "People": "UB_PEOPLE_DS_ID",
    "Books": "UB_BOOKS_DS_ID",
    "Reading Log": "UB_READING_LOG_DS_ID",
    "Genres": "UB_GENRES_DS_ID",
    "Recipes": "UB_RECIPES_DS_ID",
    "Meal Planner": "UB_MEAL_PLANNER_DS_ID",
}


# ---------------------------------------------------------------------------
# Config dataclass — loaded once at startup
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UBConfig:
    """Validated configuration loaded from environment variables."""

    notion_secret: str

    # Primary data source IDs (required)
    tasks_ds_id: str
    projects_ds_id: str
    notes_ds_id: str
    tags_ds_id: str
    goals_ds_id: str

    # IANA timezone name used to resolve "today" / "tomorrow" / "now" inside
    # workflow tools like daily_review_snapshot. Validated at load time.
    timezone: str = "UTC"

    # Secondary data source IDs (optional) — name → ds_id
    secondary_ds: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> UBConfig:
        """Load config from environment variables. Raises ValueError if required vars missing."""
        notion_secret = os.environ.get("NOTION_INTEGRATION_SECRET", "")
        tasks = os.environ.get("UB_TASKS_DS_ID", "")
        projects = os.environ.get("UB_PROJECTS_DS_ID", "")
        notes = os.environ.get("UB_NOTES_DS_ID", "")
        tags = os.environ.get("UB_TAGS_DS_ID", "")
        goals = os.environ.get("UB_GOALS_DS_ID", "")

        missing = []
        if not notion_secret:
            missing.append("NOTION_INTEGRATION_SECRET")
        if not tasks:
            missing.append("UB_TASKS_DS_ID")
        if not projects:
            missing.append("UB_PROJECTS_DS_ID")
        if not notes:
            missing.append("UB_NOTES_DS_ID")
        if not tags:
            missing.append("UB_TAGS_DS_ID")
        if not goals:
            missing.append("UB_GOALS_DS_ID")
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        # Workspace timezone — UB_TIMEZONE wins, then TZ, then UTC.
        # Validated against the system tz database; typos fail fast at startup.
        tz_name = (
            os.environ.get("UB_TIMEZONE")
            or os.environ.get("TZ")
            or "UTC"
        )
        try:
            ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(
                f"Invalid timezone {tz_name!r}: {e}. "
                f"Set UB_TIMEZONE to an IANA name like 'Europe/London' or 'America/New_York'."
            ) from e

        # Discover configured secondary databases
        secondary: dict[str, str] = {}
        for name, env_var in SECONDARY_DB_ENV_MAP.items():
            val = os.environ.get(env_var, "")
            if val:
                secondary[name] = val

        return cls(
            notion_secret=notion_secret,
            tasks_ds_id=tasks,
            projects_ds_id=projects,
            notes_ds_id=notes,
            tags_ds_id=tags,
            goals_ds_id=goals,
            timezone=tz_name,
            secondary_ds=secondary,
        )
