# AGENTS.md

This project keeps a single source of truth for AI-agent and contributor
guidance in **[CLAUDE.md](CLAUDE.md)** — architecture, commands, testing, and
conventions all live there.

If your agent tooling reads `AGENTS.md`, treat `CLAUDE.md` as the canonical
instructions and follow it.

(We use a thin pointer here instead of a symlink so the file works cleanly on
Windows checkouts and across tools that don't resolve symlinks.)
