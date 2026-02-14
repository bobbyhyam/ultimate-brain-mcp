"""Async httpx wrapper for the Notion API (version 2025-09-03)."""

from __future__ import annotations

import httpx

NOTION_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"


class NotionAPIError(Exception):
    """Raised when the Notion API returns a non-2xx response."""

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        super().__init__(message)


class NotionClient:
    """Lightweight async Notion API client using httpx."""

    def __init__(self, secret: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=NOTION_BASE,
            headers={
                "Authorization": f"Bearer {secret}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.is_success:
            return
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        code = body.get("code", "unknown")
        message = body.get("message", resp.text)
        raise NotionAPIError(resp.status_code, code, message)

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
        resp = await self._client.post(f"/data_sources/{ds_id}/query", json=body)
        self._raise_for_status(resp)
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

        If *children* is provided, the blocks are included as initial page body content.
        """
        body: dict = {
            "parent": {"data_source_id": ds_id},
            "properties": properties,
        }
        if children:
            body["children"] = children
        resp = await self._client.post("/pages", json=body)
        self._raise_for_status(resp)
        return resp.json()

    async def get_page(self, page_id: str) -> dict:
        """GET /v1/pages/{page_id}"""
        resp = await self._client.get(f"/pages/{page_id}")
        self._raise_for_status(resp)
        return resp.json()

    async def update_page(self, page_id: str, properties: dict) -> dict:
        """PATCH /v1/pages/{page_id}"""
        resp = await self._client.patch(f"/pages/{page_id}", json={"properties": properties})
        self._raise_for_status(resp)
        return resp.json()

    # ------------------------------------------------------------------
    # Blocks (for reading page content)
    # ------------------------------------------------------------------

    async def get_blocks(self, block_id: str, *, page_size: int = 100) -> list[dict]:
        """GET /v1/blocks/{block_id}/children — returns all child blocks (paginated)."""
        all_blocks: list[dict] = []
        cursor: str | None = None
        for _ in range(10):  # safety limit
            params: dict = {"page_size": page_size}
            if cursor:
                params["start_cursor"] = cursor
            resp = await self._client.get(f"/blocks/{block_id}/children", params=params)
            self._raise_for_status(resp)
            data = resp.json()
            all_blocks.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return all_blocks

    async def append_blocks(self, block_id: str, children: list[dict]) -> list[dict]:
        """PATCH /v1/blocks/{block_id}/children — append child blocks.

        Auto-batches at 100 blocks per request (Notion API limit).
        Returns the list of created blocks.
        """
        created: list[dict] = []
        for i in range(0, len(children), 100):
            batch = children[i:i + 100]
            resp = await self._client.patch(
                f"/blocks/{block_id}/children", json={"children": batch}
            )
            self._raise_for_status(resp)
            created.extend(resp.json().get("results", []))
        return created

    async def delete_block(self, block_id: str) -> None:
        """DELETE /v1/blocks/{block_id} — delete (archive) a single block."""
        resp = await self._client.delete(f"/blocks/{block_id}")
        self._raise_for_status(resp)

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
            resp = await self._client.post("/search", json=body)
            self._raise_for_status(resp)
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
        resp = await self._client.get(f"/databases/{database_id}")
        self._raise_for_status(resp)
        return resp.json()
