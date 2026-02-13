#!/usr/bin/env python3
"""Convenience wrapper — delegates to the packaged setup_client module.

Usage:
    uv run python setup_client.py --client claude-code --scope project
    uv run python setup_client.py --client claude-code --scope user
    uv run python setup_client.py --client claude-desktop

Once published, users can run this directly via:
    uvx --from ultimate-brain-mcp ultimate-brain-setup --client claude-code --scope project
"""

from ultimate_brain_mcp.setup_client import main

if __name__ == "__main__":
    main()
