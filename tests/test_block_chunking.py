"""Unit tests for the chunking + depth-flattening helpers in notion_client.

No Notion credentials needed — these test pure data manipulation.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ultimate_brain_mcp.formatters import _make_rich_text, text_to_blocks
from ultimate_brain_mcp.notion_client import (
    MAX_CHILDREN_PER_ARRAY,
    NotionAPIError,
    NotionClient,
    PartialWriteError,
    _RateLimiter,
    _split_for_depth,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bullet(text: str, *, children: list[dict] | None = None) -> dict:
    """Build a minimal bulleted_list_item block for tests."""
    block: dict = {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"text": {"content": text}}]},
    }
    if children is not None:
        block["bulleted_list_item"]["children"] = children
    return block


# ---------------------------------------------------------------------------
# _split_for_depth
# ---------------------------------------------------------------------------


class TestSplitForDepth:
    def test_flat_passes_through(self):
        """Depth-1 input — no children anywhere — should be unchanged."""
        blocks = [_bullet("A"), _bullet("B"), _bullet("C")]
        top, deferred = _split_for_depth(blocks)
        assert deferred == []
        assert len(top) == 3
        assert top[0]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "A"

    def test_depth_2_passes_through(self):
        """Depth-2 input — top-level with one level of children — fits in one request."""
        blocks = [_bullet("Parent", children=[_bullet("Child A"), _bullet("Child B")])]
        top, deferred = _split_for_depth(blocks)
        assert deferred == []
        assert len(top) == 1
        kids = top[0]["bulleted_list_item"]["children"]
        assert [k["bulleted_list_item"]["rich_text"][0]["text"]["content"] for k in kids] == [
            "Child A",
            "Child B",
        ]

    def test_depth_3_defers_grandchildren(self):
        """Depth-3 input — grandchildren get peeled off at depth 2."""
        grandchildren = [_bullet("Grandchild")]
        blocks = [
            _bullet(
                "Parent",
                children=[_bullet("Child", children=grandchildren)],
            )
        ]
        top, deferred = _split_for_depth(blocks)

        # Top tree should be trimmed to depth 2 — the child no longer has children.
        child = top[0]["bulleted_list_item"]["children"][0]
        assert "children" not in child["bulleted_list_item"]

        # The grandchildren are deferred under path [parent_idx, child_idx] = [0, 0]
        assert len(deferred) == 1
        path, kids = deferred[0]
        assert path == [0, 0]
        assert kids == grandchildren

    def test_depth_4_collapses_to_two_layers(self):
        """4-level deep input — the first defer takes 3+ levels; deeper layers get
        re-deferred when those deferred kids are themselves passed to append_blocks
        (handled by the caller, not this function)."""
        great = _bullet("Great-grandchild")
        grand = _bullet("Grandchild", children=[great])
        child = _bullet("Child", children=[grand])
        parent = _bullet("Parent", children=[child])

        top, deferred = _split_for_depth([parent])

        # Top tree retains parent + child but child's children are gone.
        kept_child = top[0]["bulleted_list_item"]["children"][0]
        assert kept_child["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "Child"
        assert "children" not in kept_child["bulleted_list_item"]

        # Deferred = [grandchild (still carrying great-grandchild)]
        assert len(deferred) == 1
        path, kids = deferred[0]
        assert path == [0, 0]
        assert len(kids) == 1
        assert kids[0]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "Grandchild"
        # Great-grandchild is still nested inside the deferred kids.
        # When the caller re-runs _split_for_depth on these, it'll defer further.
        assert (
            kids[0]["bulleted_list_item"]["children"][0]["bulleted_list_item"]["rich_text"][0][
                "text"
            ]["content"]
            == "Great-grandchild"
        )

    def test_does_not_mutate_input(self):
        original = [
            _bullet("P", children=[_bullet("C", children=[_bullet("G")])])
        ]
        snapshot_id = id(original[0]["bulleted_list_item"]["children"][0]["bulleted_list_item"]["children"])
        _split_for_depth(original)

        # Input still has the grandchild
        assert (
            "children"
            in original[0]["bulleted_list_item"]["children"][0]["bulleted_list_item"]
        )
        assert (
            id(original[0]["bulleted_list_item"]["children"][0]["bulleted_list_item"]["children"])
            == snapshot_id
        )

    def test_empty_input(self):
        top, deferred = _split_for_depth([])
        assert top == []
        assert deferred == []

    def test_multiple_siblings_with_deep_children(self):
        a_grand = _bullet("A-grand")
        b_grand = _bullet("B-grand")
        blocks = [
            _bullet("A", children=[_bullet("A1", children=[a_grand])]),
            _bullet("B", children=[_bullet("B1", children=[b_grand])]),
        ]
        top, deferred = _split_for_depth(blocks)
        assert len(top) == 2
        assert len(deferred) == 2
        # Sibling A's deferred entry has path [0, 0]; B's has [1, 0].
        paths = sorted(p for p, _ in deferred)
        assert paths == [[0, 0], [1, 0]]


# ---------------------------------------------------------------------------
# _RateLimiter
# ---------------------------------------------------------------------------


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_serial_calls_are_spaced(self):
        limiter = _RateLimiter(rate=10.0)  # 100ms interval
        start = time.monotonic()
        for _ in range(3):
            await limiter.acquire()
        elapsed = time.monotonic() - start
        # 3 acquires at 100ms interval should take >= ~200ms (first is free,
        # next two each wait one interval).
        assert elapsed >= 0.18, f"expected >=0.18s, got {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_concurrent_callers_serialised(self):
        limiter = _RateLimiter(rate=10.0)
        start = time.monotonic()
        await asyncio.gather(*(limiter.acquire() for _ in range(4)))
        elapsed = time.monotonic() - start
        # 4 concurrent acquires at 100ms interval >= ~300ms
        assert elapsed >= 0.28, f"expected >=0.28s, got {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_zero_rate_disables_limiter(self):
        limiter = _RateLimiter(rate=0.0)
        start = time.monotonic()
        for _ in range(10):
            await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.05


# ---------------------------------------------------------------------------
# Retry on 429 / 5xx
# ---------------------------------------------------------------------------


def _mock_response(status: int, json_body: dict | None = None, headers: dict | None = None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.is_success = 200 <= status < 300
    resp.headers = headers or {}
    resp.json = MagicMock(return_value=json_body or {})
    resp.text = ""
    return resp


class TestRetry:
    @pytest.mark.asyncio
    async def test_retries_on_429_then_succeeds(self):
        client = NotionClient("fake-secret", rate_per_sec=1000.0)  # fast for tests
        try:
            responses = [
                _mock_response(429, headers={"Retry-After": "0"}),
                _mock_response(429, headers={"Retry-After": "0"}),
                _mock_response(200, json_body={"id": "page-1"}),
            ]
            client._client.request = AsyncMock(side_effect=responses)

            result = await client._request("GET", "/pages/page-1")
            assert result.status_code == 200
            assert client._client.request.await_count == 3
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_retries_on_503_then_succeeds(self):
        client = NotionClient("fake-secret", rate_per_sec=1000.0)
        try:
            responses = [
                _mock_response(503),
                _mock_response(200, json_body={}),
            ]
            client._client.request = AsyncMock(side_effect=responses)

            result = await client._request("GET", "/pages/page-1")
            assert result.status_code == 200
            assert client._client.request.await_count == 2
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_non_retryable_400_raises_immediately(self):
        client = NotionClient("fake-secret", rate_per_sec=1000.0)
        try:
            client._client.request = AsyncMock(
                return_value=_mock_response(400, json_body={"code": "bad", "message": "oops"})
            )

            with pytest.raises(NotionAPIError) as exc:
                await client._request("GET", "/pages/page-1")
            assert exc.value.status == 400
            assert client._client.request.await_count == 1
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_gives_up_after_max_retries(self):
        client = NotionClient("fake-secret", rate_per_sec=1000.0)
        try:
            client._client.request = AsyncMock(
                return_value=_mock_response(429, headers={"Retry-After": "0"})
            )

            with pytest.raises(NotionAPIError) as exc:
                await client._request("GET", "/pages/page-1")
            assert exc.value.status == 429
            # MAX_RETRIES=5 means up to 6 attempts
            assert client._client.request.await_count == 6
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# append_blocks chunking + PartialWriteError
# ---------------------------------------------------------------------------


class TestAppendChunking:
    @pytest.mark.asyncio
    async def test_chunks_at_100(self):
        client = NotionClient("fake-secret", rate_per_sec=1000.0)
        try:
            # 250 blocks → 3 batches: 100 + 100 + 50
            children = [_bullet(f"Item {i}") for i in range(250)]
            calls: list[dict] = []

            async def fake_request(method, url, **kwargs):
                calls.append(kwargs.get("json", {}))
                return _mock_response(200, json_body={"results": kwargs["json"]["children"]})

            client._client.request = AsyncMock(side_effect=fake_request)

            await client.append_blocks("block-id", children)

            assert len(calls) == 3
            assert len(calls[0]["children"]) == 100
            assert len(calls[1]["children"]) == 100
            assert len(calls[2]["children"]) == 50
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_partial_failure_reports_count(self):
        client = NotionClient("fake-secret", rate_per_sec=1000.0)
        try:
            children = [_bullet(f"Item {i}") for i in range(250)]

            async def fake_request(method, url, **kwargs):
                if "/blocks/" in url and "children" in url:
                    n_so_far = sum(len(c["children"]) for c in [kwargs["json"]])
                    # Succeed on first batch, fail on second.
                    if not hasattr(fake_request, "calls"):
                        fake_request.calls = 0
                    fake_request.calls += 1
                    if fake_request.calls == 1:
                        return _mock_response(
                            200, json_body={"results": kwargs["json"]["children"]}
                        )
                    return _mock_response(
                        500, json_body={"code": "internal", "message": "boom"}
                    )
                return _mock_response(200, json_body={"results": []})

            client._client.request = AsyncMock(side_effect=fake_request)

            with pytest.raises(PartialWriteError) as exc:
                await client.append_blocks("block-id", children)

            # First batch (100) succeeded; remaining 150 failed.
            assert exc.value.written == 100
            assert exc.value.remaining == 150
            assert exc.value.page_id == "block-id"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_empty_children_is_noop(self):
        client = NotionClient("fake-secret", rate_per_sec=1000.0)
        try:
            client._client.request = AsyncMock()
            result = await client.append_blocks("block-id", [])
            assert result == []
            assert client._client.request.await_count == 0
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# 2000-char rich_text chunking regression (covers code blocks + paragraphs)
# ---------------------------------------------------------------------------


class TestRichTextChunking:
    def test_long_code_block_splits_segments(self):
        long_code = "x" * 5500
        blocks = text_to_blocks(f"```python\n{long_code}\n```")
        assert len(blocks) == 1
        rich = blocks[0]["code"]["rich_text"]
        # 5500 chars / 2000 = 3 segments
        assert len(rich) == 3
        assert all(len(seg["text"]["content"]) <= 2000 for seg in rich)
        joined = "".join(seg["text"]["content"] for seg in rich)
        assert joined == long_code

    def test_huge_paragraph_chunks_correctly(self):
        # 100KB single paragraph
        chunk = "y" * 100_000
        segments = _make_rich_text(chunk, parse_markdown=False)
        assert len(segments) == 50
        assert all(len(s["text"]["content"]) <= 2000 for s in segments)
        joined = "".join(s["text"]["content"] for s in segments)
        assert joined == chunk
