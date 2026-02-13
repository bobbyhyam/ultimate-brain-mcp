import os
import sys

from .server import mcp


def main():
    required = [
        "NOTION_INTEGRATION_SECRET",
        "UB_TASKS_DS_ID",
        "UB_PROJECTS_DS_ID",
        "UB_NOTES_DS_ID",
        "UB_TAGS_DS_ID",
        "UB_GOALS_DS_ID",
    ]
    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        print(
            f"Missing required environment variables: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)
    mcp.run()
