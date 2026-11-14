"""Lever Postings API — https://github.com/lever/postings-api (global + EU instances)."""
from __future__ import annotations

from ..http import NotFound, get_json
from ..models import Company, Posting, parse_ts, strip_html
from .base import FetchContext, ProbeResult

NAME = "lever"
HOSTS = {"global": "https://api.lever.co", "eu": "https://api.eu.lever.co"}


def parse(payload: list, company: Company) -> list[Posting]:
    out = []
    for j in payload or []:
        cat = j.get("categories") or {}
        locs = cat.get("allLocations") or ([cat["location"]] if cat.get("location") else [])
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=str(j["id"]),
            title=(j.get("text") or "").strip(), url=j.get("hostedUrl", ""),
            location="; ".join(locs), country=j.get("country") or "",
            remote=(j.get("workplaceType") == "remote") if j.get("workplaceType") else None,
            department=" / ".join(x for x in (cat.get("department"), cat.get("team")) if x),
            employment_type=cat.get("commitment") or "",
            posted_at=parse_ts(j.get("createdAt")),
            description=strip_html(j.get("descriptionPlain") or j.get("description")),
            raw={k: j.get(k) for k in ("id", "text", "categories", "createdAt", "workplaceType")},
        ))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    host = HOSTS[company.config.get("region", "global")]
    data = await get_json(client, f"{host}/v0/postings/{company.slug}", params={"mode": "json"})
    return parse(data, company)


async def probe(client, slug: str) -> ProbeResult | None:
    for region, host in HOSTS.items():
        try:
            data = await get_json(client, f"{host}/v0/postings/{slug}", params={"mode": "json"}, retries=1)
        except NotFound:
            continue
        if isinstance(data, list):
            return ProbeResult(NAME, slug, len(data), config={"region": region})
    return None
