"""Unit tests for the config helpers added for daily_review_snapshot (no API calls)."""

from __future__ import annotations

import pytest

from ultimate_brain_mcp.config import UBConfig, extract_property_metadata


def _schema(properties: dict) -> dict:
    return {"properties": properties}


def test_extract_property_metadata_missing_property():
    meta = extract_property_metadata(_schema({}), "Location")
    assert meta == {"exists": False}


def test_extract_property_metadata_select_with_options():
    schema = _schema(
        {
            "Location": {
                "type": "select",
                "select": {
                    "options": [
                        {"name": "@home"},
                        {"name": "@office"},
                        {"id": "no-name-skipped"},
                    ]
                },
            }
        }
    )

    meta = extract_property_metadata(schema, "Location")

    assert meta == {
        "exists": True,
        "name": "Location",
        "type": "select",
        "options": ["@home", "@office"],
    }


def test_extract_property_metadata_multi_select():
    schema = _schema(
        {
            "Labels": {
                "type": "multi_select",
                "multi_select": {"options": [{"name": "deep-work"}]},
            }
        }
    )

    meta = extract_property_metadata(schema, "Labels")

    assert meta["type"] == "multi_select"
    assert meta["options"] == ["deep-work"]


def test_extract_property_metadata_status_with_options():
    schema = _schema(
        {
            "Location": {
                "type": "status",
                "status": {"options": [{"name": "Office"}, {"name": "Home"}]},
            }
        }
    )

    meta = extract_property_metadata(schema, "Location")

    assert meta["type"] == "status"
    assert meta["options"] == ["Office", "Home"]


def test_extract_property_metadata_other_type_returns_empty_options():
    schema = _schema({"Location": {"type": "rich_text"}})

    meta = extract_property_metadata(schema, "Location")

    assert meta == {
        "exists": True,
        "name": "Location",
        "type": "rich_text",
        "options": [],
    }


def _required_env(extra: dict | None = None) -> dict:
    base = {
        "NOTION_INTEGRATION_SECRET": "secret",
        "UB_TASKS_DS_ID": "tasks",
        "UB_PROJECTS_DS_ID": "projects",
        "UB_NOTES_DS_ID": "notes",
        "UB_TAGS_DS_ID": "tags",
        "UB_GOALS_DS_ID": "goals",
    }
    if extra:
        base.update(extra)
    return base


def test_ubconfig_invalid_timezone_raises(monkeypatch):
    """from_env raises ValueError on an unknown timezone."""
    for k, v in _required_env({"UB_TIMEZONE": "Not/A_Zone"}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TZ", raising=False)

    with pytest.raises(ValueError, match="Invalid timezone"):
        UBConfig.from_env()


def test_ubconfig_valid_timezone_loads(monkeypatch):
    """from_env accepts a valid IANA name."""
    for k, v in _required_env({"UB_TIMEZONE": "Europe/London"}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TZ", raising=False)

    config = UBConfig.from_env()
    assert config.timezone == "Europe/London"


def test_ubconfig_default_timezone_is_utc(monkeypatch):
    """No UB_TIMEZONE and no TZ → defaults to UTC."""
    for k, v in _required_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("UB_TIMEZONE", raising=False)
    monkeypatch.delenv("TZ", raising=False)

    config = UBConfig.from_env()
    assert config.timezone == "UTC"
