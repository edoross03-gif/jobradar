"""Shared async HTTP client with polite defaults + retry."""
from __future__ import annotations

import asyncio
import logging
import random

import httpx

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (X11; Linux x86_64) JobRadar/1.0 (personal job-alert bot; low frequency)"


def make_client(timeout: float = 25.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": UA, "Accept": "application/json, text/html;q=0.9, */*;q=0.8"},
        limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
    )


class NotFound(Exception):
    """Board/slug does not exist (404/410) — not an outage."""

    def __init__(self, url: str, status: int = 404):
        super().__init__(url)
        self.status = status


async def request(client: httpx.AsyncClient, method: str, url: str, *, retries: int = 3, **kw) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            r = await client.request(method, url, **kw)
            if r.status_code in (404, 410):
                raise NotFound(url, r.status_code)
            if r.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
            r.raise_for_status()
            return r
        except NotFound:
            raise
        except (httpx.HTTPError,) as e:
            last_exc = e
            if attempt < retries - 1:
                await asyncio.sleep((2 ** attempt) + random.random())
    assert last_exc is not None
    raise last_exc


async def get_json(client: httpx.AsyncClient, url: str, **kw):
    return (await request(client, "GET", url, **kw)).json()
