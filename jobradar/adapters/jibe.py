"""Jibe (iCIMS Attract) career sites, e.g. careers.ice.com — public JSON:
  GET https://<host>/api/jobs?page=N&limit=100  → {"totalCount": N, "jobs": [{"data": {slug, title, full_location, …}}]}
Job pages live at https://<host>/jobs/<slug>.

Company config:  host: careers.ice.com
"""
from __future__ import annotations

from ..http import get_json
from ..models import Company, Posting, parse_ts, strip_html
from .base import FetchContext, ProbeResult

NAME = "jibe"
PAGE = 100


def parse(payload: dict, company: Company) -> list[Posting]:
    host = company.config["host"]
    out = []
    for j in payload.get("jobs") or []:
        d = j.get("data", j)
        slug = str(d.get("slug") or d.get("req_id"))
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=slug, title=(d.get("title") or "").strip(),
            url=f"https://{host}/jobs/{slug}", location=d.get("full_location") or "", city=d.get("city") or "",
            country=d.get("country_code") or "", department=d.get("department") or "",
            posted_at=parse_ts(d.get("posted_date")), description=strip_html(d.get("description")),
            raw={k: d.get(k) for k in ("req_id", "employment_type", "country")},
        ))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    seen: dict[str, Posting] = {}
    for page in range(1, 31):
        payload = await get_json(client, f"https://{company.config['host']}/api/jobs?page={page}&limit={PAGE}")
        batch = parse(payload, company)
        for p in batch:
            seen.setdefault(p.external_id, p)
        if len(batch) < PAGE or page * PAGE >= int(payload.get("totalCount") or 0):
            break
    return list(seen.values())


async def probe(client, slug: str) -> ProbeResult | None:  # custom domains only
    return None
