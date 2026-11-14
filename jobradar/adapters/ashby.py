"""Ashby public Job Posting API — https://developers.ashbyhq.com/docs/public-job-posting-api"""
from __future__ import annotations

from ..http import NotFound, get_json
from ..models import Company, Posting, parse_ts, strip_html
from .base import FetchContext, ProbeResult

NAME = "ashby"
API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def parse(payload: dict, company: Company) -> list[Posting]:
    out = []
    for j in payload.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in (j.get("secondaryLocations") or [])]
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=str(j["id"]),
            title=(j.get("title") or "").strip(), url=j.get("jobUrl") or j.get("applyUrl") or "",
            location="; ".join(x for x in locs if x),
            city=addr.get("addressLocality") or "", country=addr.get("addressCountry") or "",
            remote=j.get("isRemote"),
            department=" / ".join(x for x in (j.get("department"), j.get("team")) if x),
            employment_type=j.get("employmentType") or "",
            posted_at=parse_ts(j.get("publishedAt")),
            description=strip_html(j.get("descriptionPlain") or j.get("descriptionHtml")),
            raw={k: j.get(k) for k in ("id", "title", "location", "employmentType", "publishedAt", "isRemote")},
        ))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    return parse(await get_json(client, API.format(slug=company.slug)), company)


async def probe(client, slug: str) -> ProbeResult | None:
    try:
        data = await get_json(client, API.format(slug=slug), retries=1)
    except NotFound:
        return None
    if not isinstance(data, dict) or "jobs" not in data:
        return None
    return ProbeResult(NAME, slug, len(data["jobs"]))
