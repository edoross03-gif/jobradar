"""amazon.jobs public search JSON. Query-based (one search per early-career term)."""
from __future__ import annotations

from datetime import datetime, timezone

from ..http import get_json
from ..models import Company, Posting, strip_html
from .base import FetchContext, ProbeResult

NAME = "amazon"
API = "https://www.amazon.jobs/en/search.json"
PAGE = 100


def _date(s: str | None):
    try:
        return datetime.strptime((s or "").strip(), "%B %d, %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse(payload: dict, company: Company) -> list[Posting]:
    out = []
    for j in payload.get("jobs", []):
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=str(j.get("id_icims") or j.get("id")),
            title=(j.get("title") or "").strip(), url="https://www.amazon.jobs" + (j.get("job_path") or ""),
            location=j.get("normalized_location") or j.get("location") or "",
            city=j.get("city") or "", country=j.get("country_code") or "",
            department=j.get("job_category") or "", posted_at=_date(j.get("posted_date")),
            description=strip_html((j.get("description_short") or "") + " " + (j.get("basic_qualifications") or "")),
            raw={k: j.get(k) for k in ("id_icims", "title", "normalized_location", "posted_date", "job_category")},
        ))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    seen: dict[str, Posting] = {}
    for term in ctx.search_terms:
        for page in range(min(ctx.max_pages, 5)):
            data = await get_json(client, API, params={
                "base_query": term, "result_limit": PAGE, "offset": page * PAGE, "sort": "recent"})
            batch = parse(data, company)
            for p in batch:
                seen.setdefault(p.external_id, p)
            if len(batch) < PAGE:
                break
    return list(seen.values())


async def probe(client, slug: str) -> ProbeResult | None:
    return None
