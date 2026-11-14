"""Eightfold AI career sites (Microsoft, Netflix, BCG student talent, …).

Two public JSON APIs, 10 results a page, searched once per early-career term (boards can hold thousands):
  v2   GET /api/apply/v2/jobs?domain=…&query=…&start=N&num=10   → {"count", "positions": [...]}
  pcsx GET /api/pcsx/search?domain=…&query=…&start=N            → {"data": {"count", "positions": [...]}}
Sites on PCSX answer v2 with {"message": "Not authorized for PCSX"}.

Company config:
  host: apply.careers.microsoft.com     domain: microsoft.com     api: pcsx | v2 (default v2)
"""
from __future__ import annotations

from urllib.parse import urlencode

from ..http import request
from ..models import Company, Posting, parse_ts
from .base import FetchContext, ProbeResult

NAME = "eightfold"
PAGE = 10


def parse(payload: dict, company: Company) -> list[Posting]:
    host = company.config["host"]
    data = payload.get("data", payload)
    out = []
    for j in data.get("positions") or []:
        locs = j.get("locations") or ([j["location"]] if j.get("location") else [])
        url = j.get("canonicalPositionUrl") or j.get("positionUrl") or f"/careers/job/{j['id']}"
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=str(j["id"]),
            title=(j.get("name") or "").strip(), url=url if url.startswith("http") else f"https://{host}{url}",
            location=" | ".join(locs), department=j.get("department") or "",
            remote=(j.get("work_location_option") or j.get("workLocationOption")) == "remote",
            posted_at=parse_ts(j.get("t_create") or j.get("postedTs") or j.get("creationTs")),
            raw={k: j.get(k) for k in ("id", "name", "locations", "department", "t_create", "postedTs")},
        ))
    return out


async def _page(client, cfg: dict, term: str, start: int) -> dict:
    if cfg.get("api") == "pcsx":
        path, params = "/api/pcsx/search", {"domain": cfg["domain"], "query": term, "location": "", "start": start}
    else:
        path, params = "/api/apply/v2/jobs", {"domain": cfg["domain"], "query": term, "start": start, "num": PAGE}
    return (await request(client, "GET", f"https://{cfg['host']}{path}?{urlencode(params)}")).json()


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    cfg = company.config
    seen: dict[str, Posting] = {}
    for term in ctx.search_terms:
        for page in range(ctx.max_pages):
            payload = await _page(client, cfg, term, page * PAGE)
            batch = parse(payload, company)
            for p in batch:
                seen.setdefault(p.external_id, p)
            count = int(payload.get("data", payload).get("count") or 0)
            if len(batch) < PAGE or (page + 1) * PAGE >= count:
                break
    return list(seen.values())


async def probe_config(client, cfg: dict) -> ProbeResult | None:
    for api in ("v2", "pcsx"):
        try:
            payload = await _page(client, {**cfg, "api": api}, "", 0)
        except Exception:  # noqa: BLE001
            continue
        data = payload.get("data", payload)
        if "positions" in data:
            return ProbeResult(NAME, cfg["domain"], int(data.get("count") or 0), config={**cfg, "api": api})
    return None


async def probe(client, slug: str) -> ProbeResult | None:  # needs host + domain, not a slug
    return None
