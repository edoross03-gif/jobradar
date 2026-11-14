"""Workday CXS JSON endpoint (used by the public *.myworkdayjobs.com career sites).

Big corps have thousands of postings, so instead of crawling everything we run one search
per early-career term (ctx.search_terms) and dedupe. Company config needs:
  host:   e.g. nvidia.wd5.myworkdayjobs.com
  tenant: e.g. nvidia
  site:   e.g. NVIDIAExternalCareerSite
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..http import NotFound, request
from ..models import Company, Posting
from .base import FetchContext, ProbeResult

NAME = "workday"
PAGE = 20  # Workday caps page size at 20
URL_RE = re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)")


def config_from_url(url: str) -> dict | None:
    m = URL_RE.search(url or "")
    if not m:
        return None
    tenant, wd, site = m.groups()
    return {"host": f"{tenant}.{wd}.myworkdayjobs.com", "tenant": tenant, "site": site}


def _posted(text: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(timezone.utc)
    t = (text or "").lower()
    if "today" in t:
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*day", t)
    return now - timedelta(days=int(m.group(1))) if m else None


def _location(j: dict, path: str) -> str:
    # Some tenants (e.g. Accenture) omit locationsText; the city is then in bulletFields[1:] and in /job/<City>/…
    if j.get("locationsText"):
        return j["locationsText"]
    parts = [str(b) for b in (j.get("bulletFields") or [])[1:]]
    m = re.match(r"/job/([^/]+)/", path)
    if m:
        parts.append(m.group(1).replace("-", " "))
    return " | ".join(dict.fromkeys(parts))


def parse(payload: dict, company: Company) -> list[Posting]:
    cfg = company.config
    out = []
    for j in payload.get("jobPostings", []):
        path = j.get("externalPath") or ""
        if not path:
            continue
        ext_id = (j.get("bulletFields") or [path.rsplit("_", 1)[-1]])[0]
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=str(ext_id),
            title=(j.get("title") or "").strip(),
            url=f"https://{cfg['host']}/{cfg['site']}{path}",
            location=_location(j, path), posted_at=_posted(j.get("postedOn", "")),
            raw={k: j.get(k) for k in ("title", "externalPath", "locationsText", "postedOn", "bulletFields")},
        ))
    return out


async def _search(client, cfg: dict, term: str, offset: int) -> dict:
    url = f"https://{cfg['host']}/wday/cxs/{cfg['tenant']}/{cfg['site']}/jobs"
    body = {"appliedFacets": {}, "limit": PAGE, "offset": offset, "searchText": term}
    r = await request(client, "POST", url, json=body, headers={"Content-Type": "application/json"})
    return r.json()


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    seen: dict[str, Posting] = {}
    for term in ctx.search_terms:
        for page in range(ctx.max_pages):
            data = await _search(client, company.config, term, page * PAGE)
            batch = parse(data, company)
            for p in batch:
                seen.setdefault(p.external_id, p)
            if len(batch) < PAGE or (page + 1) * PAGE >= int(data.get("total", 0) or 0):
                break
    return list(seen.values())


async def probe_config(client, cfg: dict) -> ProbeResult | None:
    try:
        data = await _search(client, cfg, "", 0)
    except (NotFound, Exception):
        return None
    return ProbeResult(NAME, cfg["tenant"], int(data.get("total", 0) or 0), config=cfg)


async def probe(client, slug: str) -> ProbeResult | None:  # slug-guessing isn't possible for Workday
    return None
