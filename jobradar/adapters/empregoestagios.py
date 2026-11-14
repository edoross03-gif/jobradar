"""E2 Emprego e Estágios (empregoestagios.com) — Portuguese job-news aggregator, per-company WordPress RSS feed.

Fallback for employers that publish no listing of their own (Millennium bcp, Haitong: own site = spontaneous
application form, openings go to LinkedIn — which Job Radar never touches). Items are news posts that stay up
forever, so only items newer than `max_age_days` count as open; older ones drop out and get closed.

Company config:
  feeds: [millennium-bcp]          # /emprego-empresa/<slug>/feed/ (several slugs if the site uses more than one)
  max_age_days: 60                 default_location: Portugal
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from ..http import request
from ..models import Company, Posting, strip_html
from .base import FetchContext, ProbeResult

NAME = "empregoestagios"
ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
# "Millennium bcp está a recrutar para a Unidade X" → "Unidade X" (the filter needs the role, not the headline)
HEADLINE_RE = re.compile(
    r"^.*?\b(?:está a recrutar|estão a recrutar|procura|abre candidaturas|tem vagas de emprego|recruta)"
    r"(?:\s+(?:para|no|na|nos|nas|em)(?:\s+(?:o|a|os|as))?)?\s+", re.I)


def _tag(item: str, name: str) -> str:
    m = re.search(rf"<{name}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{name}>", item, re.S)
    return m.group(1).strip() if m else ""


def parse(xml: str, company: Company, now: datetime | None = None) -> list[Posting]:
    cfg = company.config
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=int(cfg.get("max_age_days", 60)))
    out = []
    for item in ITEM_RE.findall(xml):
        link = strip_html(_tag(item, "link"))
        headline = strip_html(_tag(item, "title"))
        try:
            posted = parsedate_to_datetime(_tag(item, "pubDate"))
        except (TypeError, ValueError):
            posted = None
        if not link or not headline or (posted and posted < cutoff):
            continue
        title = HEADLINE_RE.sub("", headline, count=1).strip() or headline
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=link.rstrip("/").rsplit("/", 1)[-1],
            title=title[:1].upper() + title[1:], url=link, location=cfg.get("default_location", "Portugal"),
            posted_at=posted, description=strip_html(_tag(item, "description")), raw={"headline": headline},
        ))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    seen: dict[str, Posting] = {}
    for slug in company.config.get("feeds") or [company.slug]:
        xml = (await request(client, "GET", f"https://www.empregoestagios.com/emprego-empresa/{slug}/feed/")).text
        for p in parse(xml, company):
            seen.setdefault(p.external_id, p)
    return list(seen.values())


async def probe(client, slug: str) -> ProbeResult | None:  # not auto-probed: an aggregator, opt in per company
    return None
