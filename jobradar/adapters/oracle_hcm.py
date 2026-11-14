"""Oracle Recruiting Cloud / HCM "Candidate Experience" sites (JPMorgan, Lazard, Schroders, EFG, …).

Public REST endpoint behind every …/hcmUI/CandidateExperience/<lang>/sites/<site> page:
  GET https://<host>/hcmRestApi/resources/latest/recruitingCEJobRequisitions
      ?onlyData=true&expand=requisitionList.secondaryLocations
      &finder=findReqs;siteNumber=<site>,limit=200,offset=N,sortBy=POSTING_DATES_DESC
Pages hold at most 200 jobs. Boards are fetched in full: keyword search is fuzzy ("intern" also hits "internal")
and the location facet only lists the biggest countries, so neither is a safe way to narrow a board.

Company config:  host: jpmc.fa.oraclecloud.com     site: CX_1001     lang: en (for posting URLs)
"""
from __future__ import annotations

import re

from ..http import request
from ..models import Company, Posting, parse_ts
from .base import FetchContext, ProbeResult

NAME = "oracle_hcm"
PAGE = 200
MAX_PAGES = 60  # 12,000 jobs; JPMorgan is ~7,500
URL_RE = re.compile(r"https?://([\w.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/(\w{2})(?:-\w+)?/sites/([\w-]+)")


def config_from_url(text: str) -> dict | None:
    m = URL_RE.search(text or "")
    return {"host": m.group(1), "lang": m.group(2), "site": m.group(3)} if m else None


def parse(payload: dict, company: Company) -> list[Posting]:
    cfg = company.config
    base = f"https://{cfg['host']}/hcmUI/CandidateExperience/{cfg.get('lang', 'en')}/sites/{cfg['site']}/job"
    out = []
    for item in payload.get("items") or []:
        for r in item.get("requisitionList") or []:
            locs = [r.get("PrimaryLocation") or ""] + [s.get("Name") or "" for s in r.get("secondaryLocations") or []]
            out.append(Posting(
                company_key=company.key, source=NAME, external_id=str(r["Id"]), title=(r.get("Title") or "").strip(),
                url=f"{base}/{r['Id']}", location=" | ".join(dict.fromkeys(x for x in locs if x)),
                country=r.get("PrimaryLocationCountry") or "", remote=(r.get("WorkplaceTypeCode") == "ORA_REMOTE") or None,
                department=r.get("JobFamily") or r.get("JobFunction") or "", posted_at=parse_ts(r.get("PostedDate")),
                raw={k: r.get(k) for k in ("Id", "Title", "PrimaryLocation", "PostedDate", "WorkerType", "ContractType")},
            ))
    return out


async def _page(client, cfg: dict, offset: int) -> dict:
    url = (f"https://{cfg['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
           f"&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={cfg['site']},"
           f"limit={PAGE},offset={offset},sortBy=POSTING_DATES_DESC")
    return (await request(client, "GET", url)).json()


def _total(payload: dict) -> int:
    items = payload.get("items") or [{}]
    return int(items[0].get("TotalJobsCount") or 0)


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    seen: dict[str, Posting] = {}
    for page in range(MAX_PAGES):
        payload = await _page(client, company.config, page * PAGE)
        batch = parse(payload, company)
        for p in batch:
            seen.setdefault(p.external_id, p)
        if len(batch) < PAGE or (page + 1) * PAGE >= _total(payload):
            break
    return list(seen.values())


async def probe_config(client, cfg: dict) -> ProbeResult | None:
    try:
        payload = await _page(client, cfg, 0)
    except Exception:  # noqa: BLE001
        return None
    return ProbeResult(NAME, cfg["site"], _total(payload), config=cfg) if payload.get("items") else None


async def probe(client, slug: str) -> ProbeResult | None:  # needs host + site, found by discover's fingerprinting
    return None
