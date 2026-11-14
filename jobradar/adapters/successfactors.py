"""SAP SuccessFactors Recruiting Marketing ("RMK" / career-site builder) sites, e.g. jobs.deloitte.pt.

Two modes (company config `mode`):
  search  (default) — the server-rendered search page /search/?q=…&locationsearch=…&startrow=N, 25 rows a page.
          Big multi-country boards (EY) are narrowed per company with `locations` (server-side text match), and
          `query: true` runs one search per early-career term like Workday instead of listing the whole board.
  sitemap — newer "Unify" sites render results client-side, but still publish /sitemap.xml with every job URL.
          Titles come from the URL slug and the location from `default_location` (single-country member firms).

Company config:
  host: jobs.deloitte.pt          path: "" (EY uses "/ey")        mode: search | sitemap
  locations: [Portugal]           query: false                     default_location: Luxembourg
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from urllib.parse import unquote, urlencode

from ..http import request
from ..models import Company, Posting
from .base import FetchContext, ProbeResult

NAME = "successfactors"
ROW_SPLIT = re.compile(r'<tr class="data-row')
TITLE_RE = re.compile(r'<a[^>]*class="jobTitle-link"[^>]*>(.*?)</a>', re.S)
HREF_RE = re.compile(r'href="(/[^"]*?/job/[^"]*|/job/[^"]*)"')
LOC_RE = re.compile(r'<span class="jobLocation">\s*(.*?)\s*</span>', re.S)
DATE_RE = re.compile(r'<span class="jobDate[^"]*">\s*(.*?)\s*</span>', re.S)
JOB_ID_RE = re.compile(r"/(\d{5,})/?$")
SITEMAP_RE = re.compile(r"<loc>(https?://[^<]*/job/([^<]*?)/(\d{5,})/?)</loc>(?:\s*<lastmod>([^<]*)</lastmod>)?")
RMK_RE = re.compile(r"rmkcdn\.successfactors\.com|/platform/js/j2w/|jobs2web", re.I)


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _date(s: str) -> datetime | None:
    s = _text(s)
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y-%m-%d", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse(page_html: str, company: Company) -> list[Posting]:
    """Rows of one search-results page."""
    host = company.config["host"]
    out = []
    for chunk in ROW_SPLIT.split(page_html)[1:]:
        chunk = chunk.split("</tr>", 1)[0]
        href = HREF_RE.search(chunk)
        title = TITLE_RE.search(chunk)
        if not (href and title):
            continue
        path = html.unescape(href.group(1))
        m = JOB_ID_RE.search(path)
        if not m:
            continue
        loc, date = LOC_RE.search(chunk), DATE_RE.search(chunk)
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=m.group(1),
            title=_text(title.group(1)), url=f"https://{host}{path}",
            location=_text(loc.group(1)) if loc else "", posted_at=_date(date.group(1)) if date else None,
            raw={"path": path},
        ))
    return out


def parse_sitemap(xml: str, company: Company) -> list[Posting]:
    loc = company.config.get("default_location", "")
    out = []
    for url, slug, job_id, lastmod in SITEMAP_RE.findall(xml):
        title = re.sub(r"\s+", " ", unquote(html.unescape(slug)).replace("-", " ")).strip()
        # Many sites prefix the slug with the city ("Luxembourg-Intern-Debt-Advisory").
        if loc and title.lower().startswith(loc.lower() + " "):
            title = title[len(loc) + 1:]
        out.append(Posting(company_key=company.key, source=NAME, external_id=job_id, title=title,
                           url=html.unescape(url), location=loc, posted_at=_date(lastmod) if lastmod else None,
                           raw={"slug": slug}))
    return out


async def _search(client, cfg: dict, q: str, location: str, startrow: int) -> str:
    params = {"q": q, "startrow": startrow}
    if location:
        params["locationsearch"] = location
    url = f"https://{cfg['host']}{cfg.get('path', '')}/search/?{urlencode(params)}"
    return (await request(client, "GET", url)).text


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    cfg = company.config
    if cfg.get("mode") == "sitemap":
        xml = (await request(client, "GET", f"https://{cfg['host']}/sitemap.xml")).text
        return parse_sitemap(xml, company)
    seen: dict[str, Posting] = {}
    terms = ctx.search_terms if cfg.get("query") else [""]
    for location in cfg.get("locations") or [""]:
        for term in terms:
            startrow, page_size = 0, None
            for _ in range(ctx.max_pages if cfg.get("query") else 60):  # 60 × 25 covers EY UK (~1,000 jobs)
                batch = parse(await _search(client, cfg, term, location, startrow), company)
                fresh = [p for p in batch if p.external_id not in seen]
                for p in batch:
                    seen.setdefault(p.external_id, p)
                page_size = page_size or len(batch)
                # Past the last page RMK repeats the final page or returns nothing.
                if not batch or not fresh or len(batch) < page_size:
                    break
                startrow += len(batch)
    return list(seen.values())


async def probe_url(client, url: str) -> ProbeResult | None:
    """Used by `discover`: is this careers page an RMK site with a working search page?"""
    from urllib.parse import urlparse
    host = urlparse(url).netloc
    cfg = {"host": host}
    try:
        page = await _search(client, cfg, "", "", 0)
    except Exception:  # noqa: BLE001
        return None
    if not RMK_RE.search(page):
        return None
    rows = parse(page, Company(key="probe", name="probe", config=cfg))
    if not rows:
        try:
            xml = (await request(client, "GET", f"https://{host}/sitemap.xml")).text
        except Exception:  # noqa: BLE001
            return None
        rows = parse_sitemap(xml, Company(key="probe", name="probe", config=cfg))
        cfg["mode"] = "sitemap"
    return ProbeResult(NAME, host, len(rows), config=cfg) if rows else None


async def probe(client, slug: str) -> ProbeResult | None:  # no slug-guessing: RMK sites live on custom domains
    return None
