"""Pinpoint (<company>.pinpointhq.com) — public JSON of all live postings: GET /postings.json → {"data": [...]}."""
from __future__ import annotations

from ..http import get_json
from ..models import Company, Posting, parse_ts, strip_html
from .base import FetchContext, ProbeResult

NAME = "pinpoint"


def parse(payload: dict, company: Company) -> list[Posting]:
    out = []
    for j in payload.get("data") or []:
        loc = j.get("location") or {}
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=str(j["id"]), title=(j.get("title") or "").strip(),
            url=j.get("url") or "", location=loc.get("name") or loc.get("city") or "", city=loc.get("city") or "",
            remote=(j.get("workplace_type") == "remote") or None,
            employment_type=j.get("employment_type_text") or j.get("employment_type") or "",
            posted_at=parse_ts(j.get("created_at")), description=strip_html(j.get("description")),
            raw={k: j.get(k) for k in ("id", "employment_type", "workplace_type", "deadline_at")},
        ))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    return parse(await get_json(client, f"https://{company.slug}.pinpointhq.com/postings.json"), company)


async def probe(client, slug: str) -> ProbeResult | None:
    try:
        payload = await get_json(client, f"https://{slug}.pinpointhq.com/postings.json", retries=1)
    except Exception:  # noqa: BLE001
        return None
    return ProbeResult(NAME, slug, len(payload.get("data") or []))
