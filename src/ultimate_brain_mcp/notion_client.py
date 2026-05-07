"""Async httpx wrapper for the Notion API (version 2025-09-03).

This module abstracts away three Notion API limits so callers can submit
arbitrarily large/deep documents:

1. Children-array cap: any individual `children` array is limited to 100
   elements, including the top-level array on `POST /pages` and
   `PATCH /blocks/{id}/children`.
2. Nesting cap: a single request may contain at most 2 levels of block
   nesting. Deeper subtrees are deferred and re-appended after the parent
   block id is known.
3. Rate limit: Notion documents an average of 3 requests/sec. A
   per-client token-spaced limiter serialises requests, and 429 responses
   trigger Retry-After sleeps with exponential backoff.

See https://developers.notion.com/reference/request-limits.
"""

from __future__ import annotations

import asyncio
import copy

import httpx

NOTION_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"

# Notion-documented limits (https://developers.notion.com/reference/request-limits)
MAX_CHILDREN_PER_ARRAY = 100
MAX_NESTING_DEPTH = 2
DEFAULT_RATE_PER_SEC = 3.0
RETRY_STATUSES = {429, 502, 503, 504}
MAX_RETRIES = 5
MAX_BACKOFF_SECONDS = 30.0

# Read-side safety cap. Notion has no documented limit on blocks per page,
# but unbounded recursion on a corrupted structure would be costly. ~5000
# blocks comfortably exceeds typical document size.
MAX_READ_PAGES = 50  # 50 pages * 100 blocks/page = 5000 blocks


class NotionAPIError(Exception):
    """Raised when the Notion API returns a non-2xx response."""

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        super().__init__(message)


class PartialWriteError(NotionAPIError):
    """Raised when a chunked write fails after some blocks were already written.

    Carries the underlying error plus counts so the caller can report or
    resume from where it stopped.
    """

    def __init__(
        self,
        original: NotionAPIError,
        *,
        written: int,
        remaining: int,
        page_id: str = "",
    ) -> None:
        super().__init__(original.status, original.code, str(original))
        self.written = written
        self.remaining = remaining
        self.page_id = page_id


# ---------------------------------------------------------------------------
# Block helpers
# ---------------------------------------------------------------------------


def _block_data(block: dict) -> dict:
    """Return the inner data dict for a block (where rich_text / children live)."""
    btype = block.get("type", "")
    return block.get(btype, {})


def _split_for_depth(
    blocks: list[dict], *, max_depth: int = MAX_NESTING_DEPTH
) -> tuple[list[dict], list[tuple[list[int], list[dict]]]]:
    """Trim a block tree so it fits within Notion's nesting cap.

    Walks *blocks* (depth 1 = top-level) and, for any block at exactly
    *max_depth* that carries children, removes those children and records
    them in the returned `deferred` list along with the index path needed
    to locate the parent block in the trimmed tree.

    Returns ``(top_blocks, deferred)`` where:

    - ``top_blocks`` is a deep copy of *blocks* with depth ≤ *max_depth*,
      safe to send in a single Notion request.
    - ``deferred`` is a list of ``(path, children)`` tuples. Each path is
      a list of integer indices: ``path[0]`` indexes into ``top_blocks``,
      ``path[1]`` (if present) indexes into that block's children array,
      and so on.

    Does not mutate *blocks*.
    """
    top = copy.deepcopy(blocks)
    deferred: list[tuple[list[int], list[dict]]] = []

    def walk(block_list: list[dict], depth: int, path: list[int]) -> None:
        for idx, block in enumerate(block_list):
            data = _block_data(block)
            children = data.get("children")
            if not children:
                continue
            if depth >= max_depth:
                deferred.append((path + [idx], children))
                data.pop("children", None)
            else:
                walk(children, depth + 1, path + [idx])

    walk(top, depth=1, path=[])
    return top, deferred


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Serialises async callers to at most *rate* requests per second.

    Implemented as a leaky-bucket: each `acquire()` pushes a shared
    "next-allowed" timestamp forward by 1/rate seconds. Concurrent callers
    queue on the lock and sleep their share before returning.
    """

    def __init__(self, rate: float) -> None:
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._next_ok = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self._interval <= 0:
            return
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            wait = self._next_ok - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = loop.time()
            self._next_ok = max(now, self._next_ok) + self._interval


class NotionClient:
    """Lightweight async Notion API client using httpx.

    Handles the API's children-array cap (100), nesting cap (2 levels),
    and rate limit (~3 req/sec) transparently.
    """

    def __init__(
        self, secret: str, *, rate_per_sec: float = DEFAULT_RATE_PER_SEC
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=NOTION_BASE,
            headers={
                "Authorization": f"Bearer {secret}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        self._limiter = _RateLimiter(rate_per_sec)

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal: rate-limited request with retry on 429/5xx
    # ------------------------------------------------------------------

    async def _request(
        self, method: str, url: str, **kwargs: object
    ) -> httpx.Response:
        backoff = 1.0
        last_error: NotionAPIError | None = None
        for attempt in range(MAX_RETRIES + 1):
            await self._limiter.acquire()
            resp = await self._client.request(method, url, **kwargs)
            if resp.is_success:
                return resp
            if resp.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
                retry_after = self._retry_after_seconds(resp, fallback=backoff)
                await asyncio.sleep(min(retry_after, MAX_BACKOFF_SECONDS))
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                last_error = self._build_error(resp)
                continue
            self._raise_for_status(resp)
        # Exhausted retries
        if last_error is not None:
            raise last_error
        raise NotionAPIError(0, "unknown", "request failed without response")

    @staticmethod
    def _retry_after_seconds(resp: httpx.Response, *, fallback: float) -> float:
        header = resp.headers.get("Retry-After")
        if not header:
            return fallback
        try:
            return float(header)
        except ValueError:
            return fallback

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.is_success:
            return
        raise self._build_error(resp)

    @staticmethod
    def _build_error(resp: httpx.Response) -> NotionAPIError:
        body: dict = {}
        if resp.headers.get("content-type", "").startswith("application/json"):
            try:
                body = resp.json()
            except ValueError:
                body = {}
        code = body.get("code", "unknown")
        message = body.get("message", resp.text)
        return NotionAPIError(resp.status_code, code, message)

    # ------------------------------------------------------------------
    # Query data source (replaces /databases/{id}/query in 2025-09-03)
    # ------------------------------------------------------------------

    async def query_data_source(
        self,
        ds_id: str,
        *,
        filter: dict | None = None,
        sorts: list[dict] | None = None,
        page_size: int = 100,
        start_cursor: str | None = None,
    ) -> dict:
        """POST /v1/data_sources/{ds_id}/query — returns the raw response dict."""
        body: dict = {"page_size": page_size}
        if filter:
            body["filter"] = filter
        if sorts:
            body["sorts"] = sorts
        if start_cursor:
            body["start_cursor"] = start_cursor
        resp = await self._request("POST", f"/data_sources/{ds_id}/query", json=body)
        return resp.json()

    async def query_all(
        self,
        ds_id: str,
        *,
        filter: dict | None = None,
        sorts: list[dict] | None = None,
        max_pages: int = 5,
    ) -> list[dict]:
        """Paginate through all results (up to max_pages pages). Returns flat list of pages."""
        all_results: list[dict] = []
        cursor: str | None = None
        for _ in range(max_pages):
            data = await self.query_data_source(
                ds_id, filter=filter, sorts=sorts, start_cursor=cursor
            )
            all_results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return all_results

    # ------------------------------------------------------------------
    # Page CRUD
    # ------------------------------------------------------------------

    async def create_page(
        self, ds_id: str, properties: dict, *, children: list[dict] | None = None
    ) -> dict:
        """POST /v1/pages — create a page in the given data source.

        Accepts arbitrarily large/deep *children*. Internally chunks the
        first 100 top-level blocks into the create call, appends the
        remainder, and re-appends any subtrees deeper than 2 levels.
        """
        body: dict = {
            "parent": {"data_source_id": ds_id},
            "properties": properties,
        }

        if not children:
            resp = await self._request("POST", "/pages", json=body)
            return resp.json()

        top, deferred = _split_for_depth(children)
        first_batch = top[:MAX_CHILDREN_PER_ARRAY]
        rest = top[MAX_CHILDREN_PER_ARRAY:]

        body["children"] = first_batch
        try:
            resp = await self._request("POST", "/pages", json=body)
        except NotionAPIError as e:
            raise PartialWriteError(
                e, written=0, remaining=len(top), page_id=""
            ) from e

        page = resp.json()
        page_id = page["id"]
        written = len(first_batch)

        if rest:
            try:
                await self.append_blocks(page_id, rest)
            except NotionAPIError as e:
                # Determine how much of rest was written
                already = getattr(e, "written", 0) if isinstance(e, PartialWriteError) else 0
                raise PartialWriteError(
                    e,
                    written=written + already,
                    remaining=len(top) - written - already,
                    page_id=page_id,
                ) from e
            written += len(rest)

        if deferred:
            try:
                top_blocks = await self.get_blocks(page_id)
                await self._flush_deferred(top_blocks, deferred)
            except NotionAPIError as e:
                # Top-level write succeeded; only deep children failed.
                raise PartialWriteError(
                    e,
                    written=written,
                    remaining=sum(len(kids) for _, kids in deferred),
                    page_id=page_id,
                ) from e

        return page

    async def get_page(self, page_id: str) -> dict:
        """GET /v1/pages/{page_id}"""
        resp = await self._request("GET", f"/pages/{page_id}")
        return resp.json()

    async def update_page(self, page_id: str, properties: dict) -> dict:
        """PATCH /v1/pages/{page_id}"""
        resp = await self._request(
            "PATCH", f"/pages/{page_id}", json={"properties": properties}
        )
        return resp.json()

    # ------------------------------------------------------------------
    # Blocks (read + write of page body content)
    # ------------------------------------------------------------------

    async def get_blocks(
        self,
        block_id: str,
        *,
        page_size: int = 100,
        recursive: bool = False,
    ) -> list[dict]:
        """GET /v1/blocks/{block_id}/children — returns child blocks, paginated.

        With ``recursive=True``, descends into every block where
        ``has_children`` is True and attaches the fetched children under
        ``block[block['type']]['children']`` so downstream formatters can
        walk the full tree.

        Capped at MAX_READ_PAGES iterations (~5000 blocks) per call as a
        safety bound.
        """
        all_blocks: list[dict] = []
        cursor: str | None = None
        for _ in range(MAX_READ_PAGES):
            params: dict = {"page_size": page_size}
            if cursor:
                params["start_cursor"] = cursor
            resp = await self._request(
                "GET", f"/blocks/{block_id}/children", params=params
            )
            data = resp.json()
            all_blocks.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        if recursive:
            for block in all_blocks:
                if not block.get("has_children"):
                    continue
                children = await self.get_blocks(
                    block["id"], page_size=page_size, recursive=True
                )
                btype = block.get("type", "")
                if btype:
                    block.setdefault(btype, {})["children"] = children

        return all_blocks

    async def append_blocks(
        self, block_id: str, children: list[dict]
    ) -> list[dict]:
        """PATCH /v1/blocks/{block_id}/children — append child blocks.

        Accepts arbitrarily large/deep *children*. Splits subtrees deeper
        than 2 levels into deferred follow-ups, then chunks the remaining
        top-level array into 100-block batches per the API's children-array
        cap. Returns the flat list of created top-level blocks (with ids).

        Raises :class:`PartialWriteError` if a batch fails after earlier
        batches succeeded.
        """
        if not children:
            return []

        top, deferred = _split_for_depth(children)
        created: list[dict] = []
        total = len(top)

        for i in range(0, total, MAX_CHILDREN_PER_ARRAY):
            batch = top[i : i + MAX_CHILDREN_PER_ARRAY]
            try:
                resp = await self._request(
                    "PATCH",
                    f"/blocks/{block_id}/children",
                    json={"children": batch},
                )
            except NotionAPIError as e:
                raise PartialWriteError(
                    e,
                    written=len(created),
                    remaining=total - len(created),
                    page_id=block_id,
                ) from e
            created.extend(resp.json().get("results", []))

        if deferred:
            try:
                await self._flush_deferred(created, deferred)
            except NotionAPIError as e:
                raise PartialWriteError(
                    e,
                    written=len(created),
                    remaining=sum(len(kids) for _, kids in deferred),
                    page_id=block_id,
                ) from e

        return created

    async def _flush_deferred(
        self,
        created_top: list[dict],
        deferred: list[tuple[list[int], list[dict]]],
    ) -> None:
        """Append deferred deep children to their parent blocks.

        Each entry in *deferred* is ``(path, children)`` where path indexes
        into *created_top* (and possibly into that block's inline children
        for paths of length > 1). Notion's create/append responses don't
        include nested ids, so for paths longer than 1 we fetch the parent's
        children to resolve the depth-2 block id.
        """
        # Group by top-level index to amortise the per-parent GET.
        by_top: dict[int, list[tuple[list[int], list[dict]]]] = {}
        for path, kids in deferred:
            if not path:
                continue
            by_top.setdefault(path[0], []).append((path[1:], kids))

        for top_idx, entries in by_top.items():
            if top_idx >= len(created_top):
                continue
            top_id = created_top[top_idx]["id"]

            direct: list[list[dict]] = []
            nested: list[tuple[list[int], list[dict]]] = []
            for sub_path, kids in entries:
                if sub_path:
                    nested.append((sub_path, kids))
                else:
                    direct.append(kids)

            for kids in direct:
                await self.append_blocks(top_id, kids)

            if nested:
                depth2_blocks = await self.get_blocks(top_id)
                for sub_path, kids in nested:
                    child_idx = sub_path[0]
                    if child_idx >= len(depth2_blocks):
                        continue
                    child_id = depth2_blocks[child_idx]["id"]
                    await self.append_blocks(child_id, kids)

    async def delete_block(self, block_id: str) -> None:
        """DELETE /v1/blocks/{block_id} — delete (archive) a single block."""
        await self._request("DELETE", f"/blocks/{block_id}")

    # ------------------------------------------------------------------
    # Search (used by setup_dev.py — not used at runtime)
    # ------------------------------------------------------------------

    async def search(self, query: str = "", *, filter: dict | None = None) -> list[dict]:
        """POST /v1/search"""
        body: dict = {}
        if query:
            body["query"] = query
        if filter:
            body["filter"] = filter
        all_results: list[dict] = []
        cursor: str | None = None
        for _ in range(10):
            if cursor:
                body["start_cursor"] = cursor
            resp = await self._request("POST", "/search", json=body)
            data = resp.json()
            all_results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return all_results

    # ------------------------------------------------------------------
    # Get database metadata (used by setup_dev.py)
    # ------------------------------------------------------------------

    async def get_database(self, database_id: str) -> dict:
        """GET /v1/databases/{database_id}"""
        resp = await self._request("GET", f"/databases/{database_id}")
        return resp.json()
