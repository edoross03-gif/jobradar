"""Talentsoft (Cegid) career sites, e.g. jobs.amundi.com.

The list /offre-de-emploi/liste-toutes-offres.aspx?page=N&LCID=<lcid> (50 a page) gives title + contract type but
no location, and the RSS feed stops at 20 items. So: list every offer, then fetch the detail page — whose meta
description reads "Location: Paris. Date: …" — only for early-career contracts (internship / trainee / apprentice /
graduate / VIE), which are the only rows the rules filter could want a location for.

Company config:  host: jobs.amundi.com    lcid: 2057 (English)
"""
from __future__ import annotations

import html
import re

from ..http import request
from ..models import Company, Posting
from .base import FetchContext, ProbeResult

NAME = "talentsoft"
MAX_PAGES = 20
ITEM_RE = re.compile(r'<li class="ts-offer-list-item.*?</li>', re.S)
LINK_RE = re.compile(r'<a class="ts-offer-list-item__title-link[^"]*"\s+href="([^"]*_(\d+)\.aspx)"[^>]*>(.*?)</a>', re.S)
LOCATION_RE = re.compile(r"Location:\s*([^.\"]+)\.")
EARLY_CONTRACT = re.compile(r"intern|trainee|stage|stagi|apprent|alternan|graduate|V\.?I\.?E\b", re.I)


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse(page_html: str, company: Company) -> list[Posting]:
    host = company.config["host"]
    out = []
    for item in ITEM_RE.findall(page_html):
        link = LINK_RE.search(item)
        if not link:
            continue
        title = _text(link.group(3))
        rest = _text(re.sub(r"<img[^>]*>", "", item[link.end():]))
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=link.group(2), title=title,
            url=f"https://{host}{html.unescape(link.group(1))}", employment_type=rest,
            raw={"contract": rest},
        ))
    return out


def parse_location(detail_html: str) -> str:
    m = LOCATION_RE.search(detail_html)
    return html.unescape(m.group(1)).strip() if m else ""


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    cfg = company.config
    lcid = cfg.get("lcid", 2057)
    seen: dict[str, Posting] = {}
    for page in range(1, MAX_PAGES + 1):
        page_html = (await request(client, "GET", f"https://{cfg['host']}/offre-de-emploi/liste-toutes-offres.aspx"
                                                  f"?page={page}&LCID={lcid}")).text
        rows = [p for p in parse(page_html, company) if p.external_id not in seen]
        if not rows:
            break
        for p in rows:
            seen[p.external_id] = p
    for p in seen.values():
        if EARLY_CONTRACT.search(p.employment_type):
            try:
                p.location = parse_location((await request(client, "GET", f"{p.url}?LCID={lcid}")).text)
            except Exception:  # noqa: BLE001 — a missing detail page just leaves the location empty
                pass
    return list(seen.values())


async def probe(client, slug: str) -> ProbeResult | None:  # custom domains only
    return None
