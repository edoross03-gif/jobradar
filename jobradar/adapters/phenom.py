"""Phenom People career sites (Adobe, Allianz group, …).

Search API behind every Phenom site:
  POST https://<host>/widgets  {"ddoKey": "refineSearch", "refNum": <site code>, "from": N, "size": ≤200,
                                "keywords": …, "selected_fields": {"country": ["France"]}, …}
  → {"refineSearch": {"totalHits": N, "data": {"jobs": [...]}}}
Offset paging is unreliable on these sites (every sort order skips ~10% of jobs across pages), so boards are
queried in slices that fit one page: one query per country, or a keyword that isolates a group company.

Company config:
  host: careers.allianz.com   refnum: AISAIPGB   site_country: global   lang: en_global
  countries: [Italy, …]        # one query per country (selected_fields.country)
  keywords: "Allianz Global Investors"   # or a single keyword query (can combine with countries)
"""
from __future__ import annotations

import logging

from ..http import request
from ..models import Company, Posting, parse_ts, strip_html
from .base import FetchContext, ProbeResult

NAME = "phenom"
PAGE = 200
MAX_PAGES = 10
log = logging.getLogger(__name__)


def _body(cfg: dict, start: int, keywords: str, selected: dict) -> dict:
    return {"lang": cfg.get("lang", "en_us"), "deviceType": "desktop", "country": cfg.get("site_country", "us"),
            "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "", "subsearch": "", "from": start,
            "jobs": True, "counts": True, "all_fields": [], "size": PAGE, "clearAll": False, "jdsource": "facets",
            "isSliderEnable": False, "pageId": "page15", "siteType": "external", "keywords": keywords,
            "global": True, "selected_fields": selected, "refNum": cfg["refnum"]}


def parse(payload: dict, company: Company) -> list[Posting]:
    cfg = company.config
    lang = cfg.get("lang", "en_us").split("_")[0]
    base = f"https://{cfg['host']}/{cfg.get('site_country', 'us')}/{lang}/job"
    out = []
    for j in ((payload.get("refineSearch") or {}).get("data") or {}).get("jobs") or []:
        locs = j.get("multi_location") or [j.get("location") or ""]
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=str(j["jobId"]), title=(j.get("title") or "").strip(),
            url=f"{base}/{j['jobId']}", location=" | ".join(dict.fromkeys(x for x in locs if x)),
            city=j.get("city") or "", country=j.get("country") or "", department=j.get("category") or "",
            posted_at=parse_ts(j.get("postedDate") or j.get("dateCreated")),
            description=strip_html(j.get("descriptionTeaser")),
            raw={k: j.get(k) for k in ("jobId", "jobSeqNo", "employingEntity", "type", "experienceLevel")},
        ))
    return out


async def _search(client, cfg: dict, start: int, keywords: str, selected: dict) -> dict:
    return (await request(client, "POST", f"https://{cfg['host']}/widgets", json=_body(cfg, start, keywords, selected),
                          headers={"Content-Type": "application/json"})).json()


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    cfg = company.config
    slices = [{"country": [c]} for c in cfg.get("countries") or []] or [{}]
    seen: dict[str, Posting] = {}
    for selected in slices:
        for page in range(MAX_PAGES):
            payload = await _search(client, cfg, page * PAGE, cfg.get("keywords", ""), selected)
            batch = parse(payload, company)
            for p in batch:
                seen.setdefault(p.external_id, p)
            total = int((payload.get("refineSearch") or {}).get("totalHits") or 0)
            if (page + 1) * PAGE >= total or not batch:
                break
            if page == 0:
                log.warning("%s: slice %s has %d jobs (> %d) — paging, which may skip some", company.key, selected, total, PAGE)
    return list(seen.values())


async def probe(client, slug: str) -> ProbeResult | None:  # needs host + refNum from the site's HTML
    return None
