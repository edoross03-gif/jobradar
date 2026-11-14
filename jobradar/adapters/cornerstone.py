"""Cornerstone OnDemand career sites (<corp>.csod.com, e.g. BIL).

The career-site page embeds a short-lived bearer token and the regional API base ("cloud"); the job search is
  POST <cloud>rec-job-search/external/jobs  {"careerSiteId": 1, "pageNumber": N, "pageSize": 100, …}
  → {"data": {"totalCount": N, "requisitions": [{requisitionId, displayJobTitle, locations: [{country, city}]}]}}

Company config:  corp: bil    site: 1
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..http import request
from ..models import Company, Posting, strip_html
from .base import FetchContext, ProbeResult

NAME = "cornerstone"
PAGE = 100
TOKEN_RE = re.compile(r'"token"\s*:\s*"([^"]+)"')
CLOUD_RE = re.compile(r'"cloud"\s*:\s*"(https://[^"]+)"')


def _date(s: str | None) -> datetime | None:
    try:
        return datetime.strptime(s or "", "%m/%d/%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse(payload: dict, company: Company) -> list[Posting]:
    corp, site = company.config.get("corp", company.slug), company.config.get("site", 1)
    out = []
    for r in (payload.get("data") or {}).get("requisitions") or []:
        locs = [", ".join(x for x in (l.get("city"), l.get("state"), l.get("country")) if x) for l in r.get("locations") or []]
        countries = [l.get("country") for l in r.get("locations") or [] if l.get("country")]
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=str(r["requisitionId"]),
            title=(r.get("displayJobTitle") or "").strip(),
            url=f"https://{corp}.csod.com/ux/ats/careersite/{site}/home/requisition/{r['requisitionId']}?c={corp}",
            location=" | ".join(x for x in locs if x), country=countries[0] if len(countries) == 1 else "",
            posted_at=_date(r.get("postingEffectiveDate")), description=strip_html(r.get("externalDescription")),
            raw={k: r.get(k) for k in ("requisitionId", "postingEffectiveDate", "locations")},
        ))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    corp, site = company.config.get("corp", company.slug), company.config.get("site", 1)
    page_html = (await request(client, "GET", f"https://{corp}.csod.com/ux/ats/careersite/{site}/home?c={corp}")).text
    token, cloud = TOKEN_RE.search(page_html), CLOUD_RE.search(page_html)
    if not (token and cloud):
        raise RuntimeError("cornerstone: no token/cloud in career-site page")
    seen: dict[str, Posting] = {}
    for page in range(1, 21):
        body = {"careerSiteId": site, "careerSitePageId": site, "pageNumber": page, "pageSize": PAGE, "cultureId": 1,
                "searchText": "", "cultureName": "en-US", "states": [], "countryCodes": [], "cities": [], "placeID": "",
                "radius": None, "postingsWithinDays": None, "customFieldCheckboxKeys": [], "customFieldDropdowns": [],
                "customFieldRadios": []}
        payload = (await request(client, "POST", f"{cloud.group(1).rstrip('/')}/rec-job-search/external/jobs", json=body,
                                 headers={"Authorization": f"Bearer {token.group(1)}"})).json()
        batch = parse(payload, company)
        for p in batch:
            seen.setdefault(p.external_id, p)
        if len(batch) < PAGE or page * PAGE >= int((payload.get("data") or {}).get("totalCount") or 0):
            break
    return list(seen.values())


async def probe(client, slug: str) -> ProbeResult | None:  # opt in per company (corp id is not guessable reliably)
    return None
