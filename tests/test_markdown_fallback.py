"""Unit tests for the Markdown-endpoint fallback paths.

These run without Notion credentials: the NotionClient is replaced with a fake
whose markdown methods raise NotionAPIError, forcing the block-based fallback so
that safety net stays exercised even on workspaces where 2026-03-11 works.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from ultimate_brain_mcp import server
from ultimate_brain_mcp.formatters import blocks_to_text
from ultimate_brain_mcp.notion_client import NotionAPIError


@dataclass
class FakeClient:
    """Records calls and lets each method's behaviour be configured per-test."""

    markdown_raises: bool = False
    # Status used when markdown_raises is True. 400/404 mean "endpoint/version
    # unavailable" (fall back); others (403/5xx) must propagate.
    markdown_error_status: int = 400
    page_markdown: dict = field(default_factory=lambda: {"markdown": ""})
    blocks: list = field(default_factory=list)
    # old_str values that update_page_markdown should treat as matching.
    matchable: set = field(default_factory=set)
    calls: list = field(default_factory=list)

    def _markdown_error(self) -> NotionAPIError:
        return NotionAPIError(self.markdown_error_status, "validation_error", "version unavailable")

    async def get_page_markdown(self, page_id):
        self.calls.append(("get_page_markdown", page_id))
        if self.markdown_raises:
            raise self._markdown_error()
        return self.page_markdown

    async def replace_page_markdown(self, page_id, markdown, *, allow_deleting_content=False):
        self.calls.append(("replace_page_markdown", page_id))
        if self.markdown_raises:
            raise self._markdown_error()
        return {"object": "page_markdown"}

    async def update_page_markdown(self, page_id, content_updates, *, allow_deleting_content=False):
        # The tool applies one edit at a time.
        edit = content_updates[0]
        self.calls.append(("update_page_markdown", page_id, edit["old_str"]))
        if edit["old_str"] not in self.matchable:
            raise NotionAPIError(
                400, "validation_error", f"No matches found for {edit['old_str']}."
            )
        return {"object": "page_markdown", "markdown": "updated"}

    async def get_blocks(self, block_id, *, page_size=100, recursive=False):
        self.calls.append(("get_blocks", block_id))
        return list(self.blocks)

    async def delete_block(self, block_id):
        self.calls.append(("delete_block", block_id))

    async def append_blocks(self, page_id, blocks):
        self.calls.append(("append_blocks", page_id, len(blocks)))


def _ctx(app):
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app))


def _app(client, markdown_supported=None):
    # Mirrors AppContext's mutable markdown_supported capability flag.
    return SimpleNamespace(client=client, markdown_supported=markdown_supported)


# ---------------------------------------------------------------------------
# Read fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_markdown_fallback_to_blocks():
    """When the markdown endpoint fails, reads fall back to blocks_to_text."""
    para = {
        "type": "paragraph",
        "paragraph": {"rich_text": [{"plain_text": "hello fallback"}]},
    }
    client = FakeClient(markdown_raises=True, blocks=[para])
    app = _app(client)

    text = await server._read_page_markdown(app, "pid")

    assert text == blocks_to_text([para])
    assert "hello fallback" in text
    # Confirms the fallback branch ran, not the markdown path
    assert ("get_blocks", "pid") in client.calls


@pytest.mark.asyncio
async def test_read_markdown_truncation_notice():
    """A truncated page gets a human-readable notice appended."""
    client = FakeClient(page_markdown={"markdown": "partial body", "truncated": True})
    app = _app(client)

    text = await server._read_page_markdown(app, "pid")

    assert text.startswith("partial body")
    assert "truncated by Notion" in text


@pytest.mark.asyncio
async def test_read_markdown_happy_path_no_block_fetch():
    """The normal path returns markdown and never touches get_blocks."""
    client = FakeClient(page_markdown={"markdown": "clean md", "truncated": False})
    app = _app(client)

    text = await server._read_page_markdown(app, "pid")

    assert text == "clean md"
    assert all(c[0] != "get_blocks" for c in client.calls)


# ---------------------------------------------------------------------------
# Write (replace) fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_page_content_replace_fallback_to_blocks():
    """replace mode falls back to delete-then-append when markdown PATCH fails."""
    existing = [{"id": "b1"}, {"id": "b2"}]
    client = FakeClient(markdown_raises=True, blocks=existing)
    ctx = _ctx(_app(client))

    result = await server.set_page_content(
        page_id="pid", content="New body line.", mode="replace", ctx=ctx
    )

    assert result["ok"] is True
    assert result["engine"] == "blocks"
    assert result["blocks_deleted"] == 2
    assert result["blocks_written"] >= 1
    # The markdown path was attempted first, then the block path ran
    kinds = [c[0] for c in client.calls]
    assert kinds[0] == "replace_page_markdown"
    assert "delete_block" in kinds
    assert "append_blocks" in kinds


@pytest.mark.asyncio
async def test_set_page_content_replace_markdown_happy_path():
    """When the markdown endpoint works, replace reports the markdown engine."""
    client = FakeClient(markdown_raises=False)
    ctx = _ctx(_app(client))

    result = await server.set_page_content(page_id="pid", content="Body.", mode="replace", ctx=ctx)

    assert result["ok"] is True
    assert result["engine"] == "markdown"
    assert result["blocks_written"] is None and result["blocks_deleted"] is None
    # No block manipulation on the happy path
    assert all(c[0] not in ("get_blocks", "delete_block", "append_blocks") for c in client.calls)


# ---------------------------------------------------------------------------
# H3: real errors must NOT silently fall back to the block path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429, 500, 503])
async def test_read_markdown_does_not_fallback_on_real_error(status):
    """A non-version error (auth/server) propagates instead of degrading."""
    client = FakeClient(markdown_raises=True, markdown_error_status=status, blocks=[{"id": "b"}])
    app = _app(client)

    with pytest.raises(NotionAPIError):
        await server._read_page_markdown(app, "pid")
    # Must not have attempted the block fallback
    assert all(c[0] != "get_blocks" for c in client.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 500])
async def test_set_page_content_replace_does_not_fallback_on_real_error(status):
    """replace surfaces real errors rather than running a pointless block rewrite."""
    client = FakeClient(markdown_raises=True, markdown_error_status=status, blocks=[{"id": "b"}])
    ctx = _ctx(_app(client))

    result = await server.set_page_content(page_id="pid", content="x", mode="replace", ctx=ctx)
    # _handle_api_error returns an error dict, and no block ops ran
    assert "error" in result
    assert all(c[0] not in ("get_blocks", "delete_block", "append_blocks") for c in client.calls)


# ---------------------------------------------------------------------------
# H-1: once markdown support is CONFIRMED, a 400 is a real content error and
# must surface — never silently fall back to a different (lossy) engine.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_confirmed_supported_400_surfaces_not_fallback():
    client = FakeClient(markdown_raises=True, markdown_error_status=400, blocks=[{"id": "b"}])
    app = _app(client, markdown_supported=True)  # already confirmed

    with pytest.raises(NotionAPIError):
        await server._read_page_markdown(app, "pid")
    assert all(c[0] != "get_blocks" for c in client.calls)


@pytest.mark.asyncio
async def test_replace_confirmed_supported_400_surfaces_not_fallback():
    """The dangerous case: a real content-error 400 must NOT silently write
    degraded block content while reporting success."""
    client = FakeClient(markdown_raises=True, markdown_error_status=400, blocks=[{"id": "b"}])
    ctx = _ctx(_app(client, markdown_supported=True))

    result = await server.set_page_content(page_id="pid", content="x", mode="replace", ctx=ctx)
    assert "error" in result
    assert "ok" not in result  # not a silent success
    assert all(c[0] not in ("get_blocks", "delete_block", "append_blocks") for c in client.calls)


@pytest.mark.asyncio
async def test_first_success_sets_supported_flag():
    client = FakeClient(page_markdown={"markdown": "ok", "truncated": False})
    app = _app(client)  # unknown
    await server._read_page_markdown(app, "pid")
    assert app.markdown_supported is True


@pytest.mark.asyncio
async def test_known_unsupported_skips_markdown_call():
    client = FakeClient(
        blocks=[{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "b"}]}}]
    )
    app = _app(client, markdown_supported=False)
    await server._read_page_markdown(app, "pid")
    # Never even attempted the markdown endpoint
    assert all(c[0] != "get_page_markdown" for c in client.calls)
    assert ("get_blocks", "pid") in client.calls


# ---------------------------------------------------------------------------
# M5: unrendered blocks are surfaced, not silently dropped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_markdown_unknown_block_ids_notice():
    client = FakeClient(
        page_markdown={
            "markdown": "body",
            "truncated": False,
            "unknown_block_ids": ["abc", "def"],
        }
    )
    text = await server._read_page_markdown(_app(client), "pid")
    assert text.startswith("body")
    assert "could not be rendered" in text
    assert "abc" in text and "def" in text


# ---------------------------------------------------------------------------
# patch_page_content: per-edit application + honest reporting (H2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_all_edits_match():
    client = FakeClient(matchable={"a", "b"})
    ctx = _ctx(_app(client))
    result = await server.patch_page_content(
        page_id="pid",
        edits=[{"old_str": "a", "new_str": "x"}, {"old_str": "b", "new_str": "y"}],
        ctx=ctx,
    )
    assert result["ok"] is True
    assert result["edits_applied"] == 2
    assert result["unmatched"] == []


@pytest.mark.asyncio
async def test_patch_partial_match_reports_unmatched():
    """The bug the reviewer flagged: a batch silently skips non-matches. The
    per-edit loop reports exactly which edit found no match."""
    client = FakeClient(matchable={"a"})
    ctx = _ctx(_app(client))
    result = await server.patch_page_content(
        page_id="pid",
        edits=[{"old_str": "a", "new_str": "x"}, {"old_str": "ZZZ", "new_str": "y"}],
        ctx=ctx,
    )
    assert result["ok"] is False
    assert result["edits_applied"] == 1
    assert result["unmatched"] == [{"index": 1, "old_str": "ZZZ"}]


@pytest.mark.asyncio
async def test_patch_real_error_propagates():
    """A non-'no matches' API error surfaces instead of being recorded as unmatched."""

    class BoomClient(FakeClient):
        async def update_page_markdown(
            self, page_id, content_updates, *, allow_deleting_content=False
        ):
            raise NotionAPIError(404, "object_not_found", "page not found")

    ctx = _ctx(_app(BoomClient()))
    result = await server.patch_page_content(
        page_id="pid", edits=[{"old_str": "a", "new_str": "x"}], ctx=ctx
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_patch_hard_error_midloop_reports_progress():
    """H-2: a hard error after some edits landed must report how far it got, so
    the caller knows the page was partially mutated (and not blindly retry)."""

    class FlakyClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.n = 0

        async def update_page_markdown(
            self, page_id, content_updates, *, allow_deleting_content=False
        ):
            self.n += 1
            if self.n == 1:
                return {"object": "page_markdown"}  # first edit lands
            raise NotionAPIError(403, "restricted", "permission revoked")  # then hard fail

    ctx = _ctx(_app(FlakyClient()))
    result = await server.patch_page_content(
        page_id="pid",
        edits=[{"old_str": "a", "new_str": "x"}, {"old_str": "b", "new_str": "y"}],
        ctx=ctx,
    )
    assert "error" in result
    assert result["edits_applied"] == 1
    assert result["failed_at_index"] == 1
    assert result["unmatched"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_edits, frag",
    [
        ([], "at least one"),
        ([{"old_str": "a"}], "both"),
        ([{"old_str": "a", "new_str": 5}], "strings"),
        ([{"old_str": "a", "new_str": ""}], "non-empty"),
        (["notadict"], "object"),
        ([{"old_str": "a", "new_str": "b", "replace_all_matches": "yes"}], "boolean"),
    ],
)
async def test_patch_validation_rejects_malformed(bad_edits, frag):
    ctx = _ctx(_app(FakeClient()))
    result = await server.patch_page_content(page_id="pid", edits=bad_edits, ctx=ctx)
    assert "error" in result
    assert frag in result["error"]
