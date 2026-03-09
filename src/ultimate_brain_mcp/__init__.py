import os
import sys


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

    transport = os.environ.get("MCP_TRANSPORT", "stdio")

    kwargs = {}
    if transport != "stdio":
        kwargs["host"] = os.environ.get("MCP_HOST", "0.0.0.0")
        kwargs["port"] = int(os.environ.get("MCP_PORT", "8000"))

        if os.environ.get("OAUTH_ISSUER_URL"):
            if os.environ.get("OAUTH_CLIENT_SECRET"):
                # Full OAuth AS mode (Claude.ai compatible)
                from .auth import create_oauth_provider

                provider, auth_settings = create_oauth_provider()
                kwargs["auth_server_provider"] = provider
                kwargs["auth"] = auth_settings
            else:
                # Legacy token-verifier-only mode
                from .auth import create_auth_settings, create_token_verifier

                kwargs["token_verifier"] = create_token_verifier()
                kwargs["auth"] = create_auth_settings()

    from .server import create_mcp

    server = create_mcp(**kwargs)
    server.run(transport=transport)
