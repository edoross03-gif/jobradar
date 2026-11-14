"""Bending Spoons (jobs.bendingspoons.com) — custom Next.js site; every open position is embedded in the page's
__NEXT_DATA__ (props.pageProps.list). Each position carries `suitableForStudentsAndNewGraduates`, Bending Spoons' own
early-career flag (it hires graduates into "manager" roles), which is passed on as employment_type so the rules
filter treats it as a strong early-career signal.
"""
from __future__ import annotations

import json
import re

from ..http import request
from ..models import Company, Posting, strip_html
from .base import FetchContext, ProbeResult

NAME = "bendingspoons"
HOST = "jobs.bendingspoons.com"
NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
GRAD_LABEL = "Suitable for students and new graduates"


def parse(payload: dict, company: Company) -> list[Posting]:
    out = []
    for j in ((payload.get("props") or {}).get("pageProps") or {}).get("list") or []:
        if j.get("status", "active") != "active":
            continue
        offices = j.get("officeLocations") or []
        countries = {(o.get("country") or {}).get("isoCode") for o in offices} - {None}
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=str(j["id"]), title=(j.get("jobTitle") or "").strip(),
            url=f"https://{HOST}/positions/{j['id']}", location=" | ".join(o.get("title") or "" for o in offices),
            country=countries.pop() if len(countries) == 1 else "",
            remote=bool(j.get("availableAsRemoteInDefaultCountries")) or None,
            department=", ".join(j.get("typesOfWork") or []),
            employment_type=GRAD_LABEL if j.get("suitableForStudentsAndNewGraduates") else "",
            description=strip_html(j.get("highLevelDescription")),
            raw={k: j.get(k) for k in ("id", "typesOfContract", "rotational", "suitableForStudentsAndNewGraduates")},
        ))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    page = (await request(client, "GET", f"https://{HOST}/")).text
    m = NEXT_RE.search(page)
    if not m:
        raise RuntimeError("bendingspoons: no __NEXT_DATA__ on jobs page")
    return parse(json.loads(m.group(1)), company)


async def probe(client, slug: str) -> ProbeResult | None:  # single-company adapter
    return None
