"""Greenhouse Job Board API — https://developers.greenhouse.io/job-board.html"""
from __future__ import annotations

from ..http import NotFound, get_json
from ..models import Company, Posting, parse_ts, strip_html
from .base import FetchContext, ProbeResult

NAME = "greenhouse"
HOSTS = {"global": "https://boards-api.greenhouse.io", "eu": "https://boards-api.eu.greenhouse.io"}
API = "{host}/v1/boards/{slug}/jobs"


def parse(payload: dict, company: Company) -> list[Posting]:
    out = []
    for j in payload.get("jobs", []):
        offices = j.get("offices") or []
        loc = (j.get("location") or {}).get("name", "") or "; ".join(o.get("name", "") for o in offices)
        depts = ", ".join(d.get("name", "") for d in (j.get("departments") or []) if d.get("name"))
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=str(j["id"]),
            title=j.get("title", "").strip(), url=j.get("absolute_url", ""),
            location=loc, department=depts,
            posted_at=parse_ts(j.get("first_published") or j.get("updated_at")),
            description=strip_html(j.get("content")), raw={k: j.get(k) for k in ("id", "title", "location", "updated_at", "first_published")},
        ))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    host = HOSTS[company.config.get("region", "global")]
    data = await get_json(client, API.format(host=host, slug=company.slug), params={"content": "true"})
    return parse(data, company)


async def probe(client, slug: str) -> ProbeResult | None:
    for region, host in HOSTS.items():
        try:
            data = await get_json(client, API.format(host=host, slug=slug), retries=1)
        except NotFound:
            continue
        except Exception:
            continue
        if isinstance(data, dict) and "jobs" in data:
            return ProbeResult(NAME, slug, len(data["jobs"]), config={"region": region} if region != "global" else {})
    return None
