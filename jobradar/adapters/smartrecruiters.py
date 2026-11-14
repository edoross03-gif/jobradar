"""SmartRecruiters Posting API — https://developers.smartrecruiters.com/docs/posting-api

Note: unknown company slugs return 200 + totalFound=0, so probe only accepts boards with jobs.
"""
from __future__ import annotations

from ..http import NotFound, get_json
from ..models import Company, Posting, parse_ts
from .base import FetchContext, ProbeResult

NAME = "smartrecruiters"
API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
PAGE = 100


def parse(payload: dict, company: Company) -> list[Posting]:
    out = []
    for j in payload.get("content", []):
        loc = j.get("location") or {}
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=str(j["id"]),
            title=(j.get("name") or "").strip(),
            url=f"https://jobs.smartrecruiters.com/{company.slug}/{j['id']}",
            location=loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("country")) if x),
            city=loc.get("city") or "", country=(loc.get("country") or "").upper(),
            remote=loc.get("remote"),
            department=(j.get("department") or {}).get("label", ""),
            employment_type=" / ".join(x for x in ((j.get("typeOfEmployment") or {}).get("label"),
                                                   (j.get("experienceLevel") or {}).get("label")) if x),
            posted_at=parse_ts(j.get("releasedDate")),
            raw={k: j.get(k) for k in ("id", "name", "releasedDate", "experienceLevel", "typeOfEmployment")},
        ))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    out, offset = [], 0
    for _ in range(ctx.max_pages * 5):   # up to 5k postings
        data = await get_json(client, API.format(slug=company.slug), params={"limit": PAGE, "offset": offset})
        page = parse(data, company)
        out.extend(page)
        offset += PAGE
        if offset >= int(data.get("totalFound", 0)) or not page:
            break
    return out


async def probe(client, slug: str) -> ProbeResult | None:
    try:
        data = await get_json(client, API.format(slug=slug), params={"limit": 1}, retries=1)
    except NotFound:
        return None
    n = int(data.get("totalFound", 0))
    return ProbeResult(NAME, slug, n) if n > 0 else None
