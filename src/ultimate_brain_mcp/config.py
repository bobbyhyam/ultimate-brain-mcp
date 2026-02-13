"""Environment variable loading, constants, and data source ID registry."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Status / type literals used across tools
# ---------------------------------------------------------------------------

TASK_STATUSES = ["To Do", "Doing", "Done"]
PROJECT_STATUSES = ["Not Started", "Doing", "Ongoing", "Done"]
GOAL_STATUSES = ["Active", "Achieved", "Dropped"]
TAG_TYPES = ["Area", "Resource", "Entity"]
NOTE_TYPES = ["Note", "Meeting Notes", "Brainstorm", "Journal"]
TASK_PRIORITIES = ["Low", "Medium", "High"]

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
            secondary_ds=secondary,
        )
