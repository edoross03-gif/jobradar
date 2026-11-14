"""Avature career sites (e.g. apply.deloittece.com — Deloitte Central Europe).

The /careers/SearchJobs RSS feed stops at 20 items without locations, so the server-rendered list is used:
/<lang>/careers/SearchJobs/?jobOffset=N (10–12 results a page, "Displaying 1-10 of 491 results"). Each result has
title, "City - Country" and a career-level label (Graduate / Experienced / …) that goes into employment_type.

Company config:
  host: apply.deloittece.com   lang: en_US   (lang "" for sites without a locale segment, e.g. bloomberg.avature.net)
  section: careers             # path segment before the list page (UniCredit uses "jobsuche")
  list_page: SearchJobs        # list page name (Enel uses "JobOpenings")
  countries: [Hungary]         # keep only these (location ends with " - <Country>"); for regional boards shared
                               # by several registry entries, so each posting belongs to exactly one company
"""
from __future__ import annotations

import html
import re

from ..http import request
from ..models import Company, Posting
from .base import FetchContext, ProbeResult

NAME = "avature"
MAX_PAGES = 80
# Results are <article> or <div> elements with class "article article--result"; nested divs make the closing tag
# unreliable, so the page is split at each result's opening tag instead.
RESULT_START_RE = re.compile(r'<(?:article|div) class="article article--result[^"]*"[^>]*>')
LINK_RE = re.compile(r'<a\b[^>]*href="([^"]*/JobDetail/(?:[^"?]*/)?(\d+)(?:\?[^"]*)?)"[^>]*>\s*(.*?)\s*</a>', re.S)
SPAN_RE = re.compile(r'<div class="article__header__text__subtitle[^"]*">(.*?)</div>', re.S)
TOTAL_RE = re.compile(r"of\s*([\d,.]+)\s*result", re.I)


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse(page_html: str, company: Company) -> list[Posting]:
    keep = [c.lower() for c in company.config.get("countries") or []]
    out = []
    for art in results(page_html):
        link = LINK_RE.search(art)
        if not link:
            continue
        sub = SPAN_RE.search(art)
        spans = [_text(s) for s in re.findall(r"<span[^>]*>(.*?)</span>", sub.group(1), re.S)] if sub else []
        location = spans[0] if spans else ""
        if keep and not any(location.lower().endswith(f"- {c}") or location.lower() == c for c in keep):
            continue
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=link.group(2), title=_text(link.group(3)),
            url=html.unescape(link.group(1)), location=location,
            employment_type=spans[1] if len(spans) > 1 else "",
            raw={"labels": spans[1:]},
        ))
    return out


def results(page_html: str) -> list[str]:
    return RESULT_START_RE.split(page_html)[1:]


def total(page_html: str) -> int:
    m = TOTAL_RE.search(page_html)
    return int(re.sub(r"[,.]", "", m.group(1))) if m else 0


def search_url(cfg: dict) -> str:
    lang = cfg.get("lang", "en_US")
    return (f"https://{cfg['host']}{'/' + lang if lang else ''}/{cfg.get('section', 'careers')}/"
            f"{cfg.get('list_page', 'SearchJobs')}/")


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    base = search_url(company.config)
    seen: dict[str, Posting] = {}
    all_ids: set[str] = set()  # every job on the board, before the `countries` filter
    n, offset = None, 0
    for _ in range(MAX_PAGES):
        page_html = (await request(client, "GET", f"{base}?jobOffset={offset}")).text
        n = n if n is not None else total(page_html)
        rows = len(results(page_html))  # page size differs per site (Deloitte 10, Bloomberg 12, UniCredit 15)
        page_ids = {m.group(2) for m in LINK_RE.finditer(page_html)}
        for p in parse(page_html, company):
            seen.setdefault(p.external_id, p)
        offset += rows
        # Stop at the end: no rows, past the stated total, or (sites without a total) a page with no unseen jobs.
        # Judged on all ids, not the filtered ones — a page may hold no job for this company's countries.
        if not rows or (n and offset >= n) or page_ids <= all_ids:
            break
        all_ids |= page_ids
    return list(seen.values())


async def probe(client, slug: str) -> ProbeResult | None:  # custom domains only
    return None
