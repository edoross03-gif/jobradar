"""Teamtailor career sites (TradingView, Arcano Partners, KPMG Global Services Hungary, …).

Every site — <company>.teamtailor.com or a custom domain — serves /jobs.rss with structured locations
(tt:location city/country), department and remoteStatus. The feed stops at 30 items, so when it is full the
paginated HTML list (/jobs/show_more?page=N, 20 per page) is walked too and anything missing is added from it.

Company config:  host: tradingview.teamtailor.com   (or a custom domain such as talento.arcanopartners.com)
"""
from __future__ import annotations

import html
import re
from email.utils import parsedate_to_datetime

from ..http import request
from ..models import Company, Posting, strip_html
from .base import FetchContext, ProbeResult

NAME = "teamtailor"
RSS_CAP = 30
MAX_LIST_PAGES = 25
ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
LOC_RE = re.compile(r"<tt:location>(.*?)</tt:location>", re.S)
JOB_ID_RE = re.compile(r"/jobs/(\d+)")
LI_RE = re.compile(r'<a[^>]*href="(https?://[^"]*/jobs/\d+[^"]*)"[^>]*>(.*?)</a>\s*<div[^>]*>\s*<span>([^<]*)</span>', re.S)
HOST_RE = re.compile(r"([\w-]+\.teamtailor\.com)")


def _tag(block: str, name: str) -> str:
    m = re.search(rf"<{name}>(.*?)</{name}>", block, re.S)
    return html.unescape(m.group(1).strip()) if m else ""


def parse(xml: str, company: Company) -> list[Posting]:
    out = []
    for item in ITEM_RE.findall(xml):
        link = _tag(item, "link")
        m = JOB_ID_RE.search(link)
        if not m:
            continue
        locs = []
        for loc in LOC_RE.findall(item):
            city, country = _tag(loc, "tt:city"), _tag(loc, "tt:country")
            locs.append(", ".join(x for x in (city or _tag(loc, "tt:name"), country) if x))
        try:
            posted = parsedate_to_datetime(_tag(item, "pubDate"))
        except (TypeError, ValueError):
            posted = None
        remote = _tag(item, "remoteStatus").lower()
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=m.group(1), title=_tag(item, "title"), url=link,
            location=" | ".join(dict.fromkeys(x for x in locs if x)), department=_tag(item, "tt:department"),
            remote=True if remote == "fully" else (False if remote in ("none", "onsite") else None),
            posted_at=posted, description=strip_html(_tag(item, "description")),
            raw={"remoteStatus": remote},
        ))
    return out


def parse_list(page_html: str, company: Company) -> list[Posting]:
    """Job rows of one /jobs/show_more page (title + location only)."""
    out = []
    for url, title, loc in LI_RE.findall(page_html):
        m = JOB_ID_RE.search(url)
        out.append(Posting(company_key=company.key, source=NAME, external_id=m.group(1), url=url,
                           title=strip_html(title), location=strip_html(loc), raw={"from": "list"}))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    host = company.config["host"]
    seen = {p.external_id: p for p in parse((await request(client, "GET", f"https://{host}/jobs.rss")).text, company)}
    if len(seen) >= RSS_CAP:  # feed is full: the board may be bigger, fill the gaps from the HTML list
        for page in range(1, MAX_LIST_PAGES + 1):
            rows = parse_list((await request(client, "GET", f"https://{host}/jobs/show_more?page={page}")).text, company)
            if not rows:
                break
            for p in rows:
                seen.setdefault(p.external_id, p)
    return list(seen.values())


async def probe_host(client, host: str) -> ProbeResult | None:
    try:
        r = await request(client, "GET", f"https://{host}/jobs.rss", retries=1)
    except Exception:  # noqa: BLE001
        return None
    if "rss" not in r.headers.get("content-type", "") and "<rss" not in r.text[:500]:
        return None
    return ProbeResult(NAME, host, len(parse(r.text, Company(key="probe", name="probe"))), config={"host": host})


async def probe(client, slug: str) -> ProbeResult | None:
    hit = await probe_host(client, f"{slug}.teamtailor.com")
    return hit if hit and hit.jobs else None
