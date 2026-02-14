#!/usr/bin/env python3
"""Auto-discover Ultimate Brain data sources and configure a Claude client.

Usage:
    # Claude Code (project scope — writes .mcp.json)
    uvx --from ultimate-brain-mcp ultimate-brain-setup --client claude-code --scope project

    # Claude Code (user scope — writes ~/.claude.json)
    uvx --from ultimate-brain-mcp ultimate-brain-setup --client claude-code --scope user

    # Claude Desktop (writes ~/Library/Application Support/Claude/claude_desktop_config.json)
    uvx --from ultimate-brain-mcp ultimate-brain-setup --client claude-desktop
"""

from __future__ import annotations

import argparse
import asyncio
import json
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

REQUIRED_VARS = [
    "UB_TASKS_DS_ID",
    "UB_PROJECTS_DS_ID",
    "UB_NOTES_DS_ID",
    "UB_TAGS_DS_ID",
    "UB_GOALS_DS_ID",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover Ultimate Brain data sources and configure a Claude client.",
    )
    parser.add_argument(
        "--client",
        required=True,
        choices=["claude-code", "claude-desktop"],
        help="Which Claude client to configure.",
    )
    parser.add_argument(
        "--scope",
        choices=["user", "project"],
        help="Config scope for claude-code (required for claude-code, not allowed for claude-desktop).",
    )
    args = parser.parse_args()

    if args.client == "claude-code" and args.scope is None:
        parser.error("--scope is required when --client is claude-code")
    if args.client == "claude-desktop" and args.scope is not None:
        parser.error("--scope is not allowed when --client is claude-desktop")

    return args


def get_existing_secret() -> str | None:
    """Try to read the Notion secret from a .env file in the current directory."""
    env_path = os.path.join(os.getcwd(), ".env")
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


def obtain_secret() -> str:
    """Obtain the Notion secret from env var, .env file, or user prompt."""
    secret = os.environ.get("NOTION_INTEGRATION_SECRET")
    if secret:
        print(f"Using NOTION_INTEGRATION_SECRET from environment (starts with {secret[:12]}...)\n")
        return secret

    secret = get_existing_secret()
    if secret:
        print(f"Using existing secret from .env (starts with {secret[:12]}...)\n")
        return secret

    secret = input("Enter your Notion integration secret: ").strip()
    if not secret:
        print("No secret provided.", file=sys.stderr)
        sys.exit(1)
    return secret


async def discover(secret: str) -> dict[str, str]:
    """Search for UB databases and extract data source IDs."""
    headers = {
        "Authorization": f"Bearer {secret}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(base_url=NOTION_BASE, headers=headers, timeout=30.0) as client:
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

        discovered: dict[str, str] = {}
        for ds in all_ds:
            title_parts = ds.get("title", [])
            title = "".join(t.get("plain_text", "") for t in title_parts).strip()
            env_var = DB_MAP.get(title)
            if not env_var:
                print(f"  Skipping unrecognized data source: {title!r}")
                continue
            if env_var in discovered:
                continue
            ds_id = ds["id"]
            discovered[env_var] = ds_id
            print(f"  Found {title!r} -> {env_var}={ds_id}")

        return discovered


def config_path_for(client: str, scope: str | None) -> str:
    """Return the absolute path to the target config file."""
    if client == "claude-desktop":
        return os.path.expanduser(
            "~/Library/Application Support/Claude/claude_desktop_config.json"
        )
    # claude-code
    if scope == "project":
        return os.path.join(os.getcwd(), ".mcp.json")
    # scope == "user"
    return os.path.expanduser("~/.claude.json")


def build_server_entry(secret: str, discovered: dict[str, str]) -> dict:
    """Build the MCP server config entry."""
    env: dict[str, str] = {"NOTION_INTEGRATION_SECRET": secret}
    # Add all discovered data source IDs
    for env_var in sorted(discovered):
        env[env_var] = discovered[env_var]
    return {
        "type": "stdio",
        "command": "uvx",
        "args": ["--upgrade", "ultimate-brain-mcp"],
        "env": env,
    }


def write_config(path: str, server_entry: dict) -> None:
    """Read existing config, merge in the server entry, and write back."""
    config: dict = {}
    if os.path.exists(path):
        with open(path) as f:
            try:
                config = json.load(f)
            except json.JSONDecodeError:
                print(f"Warning: {path} contains invalid JSON, starting fresh.", file=sys.stderr)
                config = {}

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    if "ultimate-brain" in config["mcpServers"]:
        print(f"  Warning: overwriting existing 'ultimate-brain' entry in {path}")

    config["mcpServers"]["ultimate-brain"] = server_entry

    # Ensure parent directory exists
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    with open(path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    print(f"\nWrote config to {path}")


async def _async_main() -> None:
    args = parse_args()

    print("=== Ultimate Brain Client Configuration ===\n")

    secret = obtain_secret()
    discovered = await discover(secret)

    # Report required/optional
    found_required = [v for v in REQUIRED_VARS if v in discovered]
    missing_required = [v for v in REQUIRED_VARS if v not in discovered]

    print(f"\nRequired: {len(found_required)}/{len(REQUIRED_VARS)} found")
    if missing_required:
        print(f"  Missing: {', '.join(missing_required)}")
        print("  Make sure these databases are shared with your integration.")
        print("  Writing config with discovered data sources anyway.\n")

    optional_found = [k for k in discovered if k not in REQUIRED_VARS]
    if optional_found:
        print(f"Optional: {len(optional_found)} found ({', '.join(optional_found)})")

    # Build and write config
    server_entry = build_server_entry(secret, discovered)
    path = config_path_for(args.client, args.scope)
    write_config(path, server_entry)

    if not missing_required:
        print("All required data sources discovered. Ready to use!")
    else:
        print("Some required data sources are missing. Check your Notion integration permissions.")


def main() -> None:
    """Sync entry point for console_scripts."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
