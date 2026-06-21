# Contributing

Thanks for your interest in improving the Ultimate Brain MCP server. This guide
covers the local development workflow and what CI expects from a pull request.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (handles Python, the virtualenv, and deps)
- Python 3.11+ (uv will install a suitable interpreter if you don't have one)

## Setup

```bash
uv sync
```

This creates a virtualenv and installs the project plus the dev tools (pytest,
ruff, mypy).

## Running tests

The suite is split into **unit** tests (no credentials needed) and **live**
tests (which talk to a real Notion workspace). The default `pytest` run executes
the unit subset only:

```bash
# Unit tests — run by default in CI, no credentials required
uv run pytest
```

The live tests are tagged `@pytest.mark.live` and deselected by default. To run
them you need a Notion workspace shared with an integration and the env vars
described in [`.env.example`](.env.example) (the test suite reads them from a
`.env` file at the repo root):

```bash
# Live integration tests — require a Notion workspace + .env (see .env.example)
uv run pytest -m live
```

## Lint, format, and type checks

```bash
uv run ruff check .          # lint
uv run ruff format .         # auto-format (use --check to verify without writing)
uv run mypy src/             # type check
```

`ruff check` and `ruff format` are configured in `pyproject.toml`; please run
both before opening a PR.

## Pull request expectations

- CI must be green. The `CI` workflow runs `ruff check`, `ruff format --check`,
  `mypy src/`, and the unit test suite across Python 3.11, 3.12, and 3.13.
- CI does **not** have Notion credentials, so it runs unit tests only. If your
  change touches live behaviour, run `uv run pytest -m live` locally and mention
  the result in your PR.
- Keep changes focused and add tests where it makes sense — prefer
  credential-free unit tests so coverage runs everywhere.

See [`CLAUDE.md`](CLAUDE.md) for a deeper tour of the architecture.
