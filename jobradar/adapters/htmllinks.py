"""Generic fallback for small custom careers pages that link each opening from one static page.

Every <a href> on `url` whose href matches `link_pattern` is a posting; its anchor text is the title and the href
(normalised) is the id. No descriptions or per-job locations — `default_location` applies to all. Use only for
server-rendered pages (check the raw HTML contains the job links) and review the dry-run: the pattern is the filter.

Company config:
  url: https://www.conio.com/jobs/posizioni-aperte
  link_pattern: /jobs/posizioni-aperte/[a-z0-9-]+$      default_location: Milan
"""
from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import urljoin, urlparse

from ..http import request
from ..models import Company, Posting
from .base import FetchContext, ProbeResult

NAME = "htmllinks"
A_RE = re.compile(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', re.S | re.I)


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse(page_html: str, company: Company) -> list[Posting]:
    cfg = company.config
    pattern = re.compile(cfg["link_pattern"])
    seen: dict[str, Posting] = {}
    for href, inner in A_RE.findall(page_html):
        url = urljoin(cfg["url"], html.unescape(href)).split("?", 1)[0].rstrip("/")
        path = urlparse(url).path
        title = _text(inner)
        if not pattern.search(path) or len(title) < 3 or path in seen:
            continue
        seen[path] = Posting(
            company_key=company.key, source=NAME, external_id=hashlib.sha1(path.encode()).hexdigest()[:16],
            title=title, url=url, location=cfg.get("default_location", ""), raw={"path": path},
        )
    return list(seen.values())


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    return parse((await request(client, "GET", company.config["url"])).text, company)


async def probe(client, slug: str) -> ProbeResult | None:  # configured by hand per company
    return None
