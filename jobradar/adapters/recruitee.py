"""Recruitee careers-site API (common among EU startups)."""
from __future__ import annotations

from ..http import NotFound, get_json
from ..models import Company, Posting, parse_ts, strip_html
from .base import FetchContext, ProbeResult

NAME = "recruitee"
API = "https://{slug}.recruitee.com/api/offers/"


def parse(payload: dict, company: Company) -> list[Posting]:
    out = []
    for j in payload.get("offers", []):
        if j.get("status") not in (None, "published"):
            continue
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=str(j["id"]),
            title=(j.get("title") or "").strip(), url=j.get("careers_url") or "",
            location=j.get("location") or "", city=j.get("city") or "", country=j.get("country") or "",
            remote=j.get("remote"), department=j.get("department") or "",
            employment_type=j.get("employment_type_code") or "",
            posted_at=parse_ts(j.get("published_at") or j.get("created_at")),
            description=strip_html(j.get("description")),
            raw={k: j.get(k) for k in ("id", "title", "location", "published_at", "employment_type_code")},
        ))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    return parse(await get_json(client, API.format(slug=company.slug)), company)


async def probe(client, slug: str) -> ProbeResult | None:
    try:
        data = await get_json(client, API.format(slug=slug), retries=1)
    except (NotFound, ValueError):
        return None
    except Exception:
        return None  # DNS failure for unknown subdomains etc.
    if not isinstance(data, dict) or "offers" not in data:
        return None
    return ProbeResult(NAME, slug, len(data["offers"]))
