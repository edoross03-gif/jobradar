"""Workable public widget API."""
from __future__ import annotations

from ..http import NotFound, get_json
from ..models import Company, Posting, parse_ts
from .base import FetchContext, ProbeResult

NAME = "workable"
API = "https://apply.workable.com/api/v1/widget/accounts/{slug}"


def parse(payload: dict, company: Company) -> list[Posting]:
    out = []
    for j in payload.get("jobs", []):
        locs = j.get("locations") or [{"city": j.get("city"), "country": j.get("country")}]
        loc_str = "; ".join(", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x) for l in locs)
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=str(j.get("shortcode") or j.get("id")),
            title=(j.get("title") or "").strip(), url=j.get("url") or j.get("shortlink") or "",
            location=loc_str, city=j.get("city") or "", country=j.get("country") or "",
            remote=bool(j.get("telecommuting")), department=j.get("department") or "",
            employment_type=j.get("employment_type") or "",
            posted_at=parse_ts(j.get("published_on") or j.get("created_at")),
            raw={k: j.get(k) for k in ("shortcode", "title", "employment_type", "published_on", "country", "city")},
        ))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    return parse(await get_json(client, API.format(slug=company.slug), params={"details": "true"}), company)


async def probe(client, slug: str) -> ProbeResult | None:
    try:
        data = await get_json(client, API.format(slug=slug), retries=1)
    except NotFound:
        return None
    if not isinstance(data, dict) or "jobs" not in data:
        return None
    return ProbeResult(NAME, slug, len(data["jobs"]), board_name=data.get("name", ""))
