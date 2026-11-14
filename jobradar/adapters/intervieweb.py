"""InRecruiting / Intervieweb (Zucchetti) — common with Italian employers (e.g. Mediobanca).

The company's own careers page embeds the public listing feed, including its access key `k`:
  GET https://<sub>.intervieweb.it/annunci.php?lang=it&d=<domain>&k=<key>&format=json_en  → [ {id, title, city, …} ]
Place names come in Italian ("Parigi", "Francia"); filters.yaml carries the Italian forms of the target cities.

Company config:  sub: mediobanca   domain: mediobanca.com   key: <k from the careers page>   lang: it
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..http import get_json
from ..models import Company, Posting, strip_html
from .base import FetchContext, ProbeResult

NAME = "intervieweb"


def _published(s: str | None) -> datetime | None:
    try:  # "09-09-2026 (11:44)"
        return datetime.strptime((s or "").split(" (")[0], "%d-%m-%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse(payload, company: Company) -> list[Posting]:
    items = payload if isinstance(payload, list) else next((v for v in (payload or {}).values() if isinstance(v, list)), [])
    out = []
    for j in items:
        place = ", ".join(x for x in (j.get("city"), j.get("nation")) if x)
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=str(j["id"]), title=(j.get("title") or "").strip(),
            url=j.get("url") or "", location=place, city=j.get("city") or "", department=j.get("function") or "",
            employment_type=j.get("contract_type") or "", posted_at=_published(j.get("published")),
            description=strip_html(j.get("description")),
            raw={k: j.get(k) for k in ("id", "published", "expires", "function")},
        ))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    c = company.config
    url = (f"https://{c['sub']}.intervieweb.it/annunci.php?lang={c.get('lang', 'it')}&d={c['domain']}"
           f"&k={c['key']}&format=json_en")
    return parse(await get_json(client, url), company)


async def probe(client, slug: str) -> ProbeResult | None:  # needs the key from the company's careers page
    return None
