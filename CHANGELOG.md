# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Contributor-readiness tooling: `.pre-commit-config.yaml` (ruff lint + format
  and basic hygiene hooks mirroring CI), Dependabot config for `uv` and
  `github-actions`, GitHub issue templates and a pull-request template, and this
  `CHANGELOG.md`. (`AGENTS.md` points agent tooling at `CLAUDE.md`.)
- Developer tooling baseline (PR #16): Ruff lint/format and mypy config, a
  `unit`/`live` pytest marker split, and a CI workflow running ruff, mypy, and
  the credential-free unit suite across Python 3.11/3.12/3.13.

### Changed

- Page body content now uses Notion's server-side Markdown endpoints, giving
  richer round-trips (tables, toggles, nesting) on `get_page_content`,
  `get_note_content`, and `set_page_content`, plus a new targeted
  find-and-replace `patch_page_content` tool.

### Removed

- In-repo `.claude/skills/ub` files; the skill now ships as the
  `ultimate-brain-mcp.skill` release asset.

## [0.5.1] - 2026-06-04

### Added

- Surface task `location` on every task read and accept `parent_task_id` on
  `update_task`.

## [0.5.0] - 2026-05-09

### Added

- `daily_review_snapshot` and `bulk_update_tasks` workflow consolidator tools.

[Unreleased]: https://github.com/bobbyhyam/ultimate-brain-mcp/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/bobbyhyam/ultimate-brain-mcp/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/bobbyhyam/ultimate-brain-mcp/releases/tag/v0.5.0
