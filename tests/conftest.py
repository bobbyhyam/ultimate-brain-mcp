"""Test fixtures — loads .env and creates shared config/client fixtures."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

from ultimate_brain_mcp.config import UBConfig
from ultimate_brain_mcp.notion_client import NotionClient


def pytest_configure(config):
    """Load .env at the very start of the test session."""
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)


@pytest.fixture
def ub_config() -> UBConfig:
    """Config loaded from .env. Skips if env vars missing."""
    required = [
        "NOTION_INTEGRATION_SECRET",
        "UB_TASKS_DS_ID",
        "UB_PROJECTS_DS_ID",
        "UB_NOTES_DS_ID",
        "UB_TAGS_DS_ID",
        "UB_GOALS_DS_ID",
    ]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        pytest.skip(f"Missing env vars: {', '.join(missing)}")
    return UBConfig.from_env()


@pytest_asyncio.fixture
async def notion_client(ub_config: UBConfig):
    """Async Notion client."""
    client = NotionClient(ub_config.notion_secret)
    yield client
    await client.close()


# ---------------------------------------------------------------------------
# Property builders (mirrors server.py helpers, kept local to avoid coupling)
# ---------------------------------------------------------------------------

def _title(text: str) -> dict:
    return {"title": [{"text": {"content": text}}]}

def _status(name: str) -> dict:
    return {"status": {"name": name}}

def _select(name: str) -> dict:
    return {"select": {"name": name}}

def _date(start: str) -> dict:
    return {"date": {"start": start}}

def _relation(ids: list[str]) -> dict:
    return {"relation": [{"id": i} for i in ids]}

def _checkbox(val: bool) -> dict:
    return {"checkbox": val}


# ---------------------------------------------------------------------------
# Seed fixtures — create test data, archive on teardown
# ---------------------------------------------------------------------------

TEST_PREFIX = "[TEST] "


@pytest_asyncio.fixture
async def seed_tag(notion_client: NotionClient, ub_config: UBConfig):
    """Create a test tag, yield it, then archive it."""
    page = await notion_client.create_page(ub_config.tags_ds_id, {
        "Name": _title(f"{TEST_PREFIX}Test Area Tag"),
        "Type": _status("Area"),
    })
    yield page
    await notion_client.update_page(page["id"], {"Archived": _checkbox(True)})


@pytest_asyncio.fixture
async def seed_goal(notion_client: NotionClient, ub_config: UBConfig, seed_tag):
    """Create a test goal linked to the seed tag, yield it, then archive it."""
    page = await notion_client.create_page(ub_config.goals_ds_id, {
        "Name": _title(f"{TEST_PREFIX}Test Goal"),
        "Status": _status("Active"),
        "Target Deadline": _date("2026-12-31"),
        "Tag": _relation([seed_tag["id"]]),
    })
    yield page
    await notion_client.update_page(page["id"], {"Archived": _checkbox(True)})


@pytest_asyncio.fixture
async def seed_project(notion_client: NotionClient, ub_config: UBConfig, seed_tag, seed_goal):
    """Create a test project linked to the seed tag and goal, yield it, then archive it."""
    page = await notion_client.create_page(ub_config.projects_ds_id, {
        "Name": _title(f"{TEST_PREFIX}Test Project"),
        "Status": _status("Doing"),
        "Target Deadline": _date("2026-06-30"),
        "Tag": _relation([seed_tag["id"]]),
        "Goal": _relation([seed_goal["id"]]),
    })
    yield page
    await notion_client.update_page(page["id"], {"Archived": _checkbox(True)})


@pytest_asyncio.fixture
async def seed_note(notion_client: NotionClient, ub_config: UBConfig, seed_tag, seed_project):
    """Create a test note linked to the seed project and tag, yield it, then archive it."""
    page = await notion_client.create_page(ub_config.notes_ds_id, {
        "Name": _title(f"{TEST_PREFIX}Test Note"),
        "Type": _select("Note"),
        "Note Date": _date(date.today().isoformat()),
        "Project": _relation([seed_project["id"]]),
        "Tag": _relation([seed_tag["id"]]),
    })
    yield page
    await notion_client.update_page(page["id"], {"Archived": _checkbox(True)})
