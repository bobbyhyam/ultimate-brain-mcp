#!/usr/bin/env python3
"""Auto-discover Ultimate Brain data source IDs and write .env file.

Usage:
    uv run python setup_dev.py
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx

NOTION_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"

# Map of UB database title → env var name
DB_MAP: dict[str, str] = {
    "Tasks": "UB_TASKS_DS_ID",
    "Projects": "UB_PROJECTS_DS_ID",
    "Notes & Docs": "UB_NOTES_DS_ID",
    "Notes": "UB_NOTES_DS_ID",
    "Tags": "UB_TAGS_DS_ID",
    "Goals": "UB_GOALS_DS_ID",
    "Work Sessions": "UB_WORK_SESSIONS_DS_ID",
    "Milestones": "UB_MILESTONES_DS_ID",
    "People": "UB_PEOPLE_DS_ID",
    "Books": "UB_BOOKS_DS_ID",
    "Reading Log": "UB_READING_LOG_DS_ID",
    "Genres": "UB_GENRES_DS_ID",
    "Recipes": "UB_RECIPES_DS_ID",
    "Meal Planner": "UB_MEAL_PLANNER_DS_ID",
}


def get_existing_secret() -> str | None:
    """Try to read the Notion secret from an existing .env file."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return None
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("NOTION_INTEGRATION_SECRET="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val and val != "secret_xxx":
                    return val
    return None


async def discover(secret: str) -> dict[str, str]:
    """Search for UB databases and extract data source IDs."""
    headers = {
        "Authorization": f"Bearer {secret}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(base_url=NOTION_BASE, headers=headers, timeout=30.0) as client:
        # Step 1: Find all data sources shared with the integration
        # In API 2025-09-03, search returns data_source objects directly
        print("Searching for data sources...")
        all_ds: list[dict] = []
        cursor: str | None = None
        for _ in range(10):
            body: dict = {"filter": {"property": "object", "value": "data_source"}}
            if cursor:
                body["start_cursor"] = cursor
            resp = await client.post("/search", json=body)
            if not resp.is_success:
                print(f"Error searching: {resp.status_code} {resp.text}", file=sys.stderr)
                sys.exit(1)
            data = resp.json()
            all_ds.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        print(f"Found {len(all_ds)} data source(s).\n")

        # Step 2: Match by title — each result is a data source with its ID
        discovered: dict[str, str] = {}
        for ds in all_ds:
            title_parts = ds.get("title", [])
            title = "".join(t.get("plain_text", "") for t in title_parts).strip()
            env_var = DB_MAP.get(title)
            if not env_var:
                print(f"  Skipping unrecognized data source: {title!r}")
                continue
            if env_var in discovered:
                continue  # already found

            ds_id = ds["id"]
            discovered[env_var] = ds_id
            print(f"  Found {title!r} → {env_var}={ds_id}")

        return discovered


def write_env(secret: str, discovered: dict[str, str]) -> None:
    """Write .env file with discovered data source IDs."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")

    # All env vars in order
    all_vars = [
        ("NOTION_INTEGRATION_SECRET", secret),
    ]
    for _title, env_var in sorted(DB_MAP.items(), key=lambda x: x[1]):
        # De-duplicate since Notes & Notes & Docs both map to UB_NOTES_DS_ID
        if any(v[0] == env_var for v in all_vars):
            continue
        all_vars.append((env_var, discovered.get(env_var, "")))

    with open(env_path, "w") as f:
        for var, val in all_vars:
            f.write(f"{var}={val}\n")

    print(f"\nWrote {env_path}")


async def main() -> None:
    print("=== Ultimate Brain Data Source Discovery ===\n")

    secret = get_existing_secret()
    if secret:
        print(f"Using existing secret from .env (starts with {secret[:12]}...)\n")
    else:
        secret = input("Enter your Notion integration secret: ").strip()
        if not secret:
            print("No secret provided.", file=sys.stderr)
            sys.exit(1)

    discovered = await discover(secret)

    write_env(secret, discovered)

    # Report
    required = ["UB_TASKS_DS_ID", "UB_PROJECTS_DS_ID", "UB_NOTES_DS_ID", "UB_TAGS_DS_ID", "UB_GOALS_DS_ID"]
    found_required = [v for v in required if v in discovered]
    missing_required = [v for v in required if v not in discovered]

    print(f"\nRequired: {len(found_required)}/{len(required)} found")
    if missing_required:
        print(f"  Missing: {', '.join(missing_required)}")
        print("  Make sure these databases are shared with your integration.")

    optional_found = [k for k in discovered if k not in required]
    if optional_found:
        print(f"Optional: {len(optional_found)} found ({', '.join(optional_found)})")

    if not missing_required:
        print("\nAll required data sources discovered. Ready to run!")
        print("  uv run ultimate-brain-mcp")
    else:
        print("\nSome required data sources are missing. Check your Notion integration permissions.")


if __name__ == "__main__":
    asyncio.run(main())
