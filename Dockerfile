FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --no-dev --extra remote --frozen

ENV MCP_TRANSPORT=streamable-http
EXPOSE 8000

CMD ["uv", "run", "ultimate-brain-mcp"]
