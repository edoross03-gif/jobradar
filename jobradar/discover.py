"""Resolve which ATS each company uses.

Strategy per company (stops at first confident hit):
  1. Fingerprint the careers page HTML (careers_url) for ATS links / embeds.
  2. Slug-probe the public APIs (Greenhouse, Ashby, Lever, SmartRecruiters, Workable, Recruitee, Personio).
Hits are validated by actually calling the API. Generic slugs can collide with another org, so every
hit is reported with sample titles/locations for a human sanity check before `--write`.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

from .adapters import ADAPTERS, PROBE_ORDER, oracle_hcm, successfactors, teamtailor, workday
from .adapters.base import FetchContext, ProbeResult
from .config import slug_candidates
from .http import make_client, request
from .models import Company

log = logging.getLogger(__name__)

FINGERPRINTS: list[tuple[str, re.Pattern]] = [
    ("greenhouse", re.compile(r"(?:boards-api|boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:v1/boards/|embed/job_board(?:/js)?\?for=)?([\w-]+)", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([\w-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)", re.I)),
    ("recruitee", re.compile(r"([\w-]+)\.recruitee\.com", re.I)),
    ("personio", re.compile(r"([\w-]+)\.jobs\.personio\.(?:de|com)", re.I)),
]
# Platforms we can detect but don't have adapters for yet (Sprint 2 backlog).
UNSUPPORTED = {
    "successfactors": re.compile(r"successfactors\.(?:com|eu)|jobs\.sap\.com|career\d*\.sapsf", re.I),
    "eightfold": re.compile(r"\.eightfold\.ai", re.I),
    "phenom": re.compile(r"phenompeople|cdn\.phenom", re.I),
    "avature": re.compile(r"\.avature\.net", re.I),
    "icims": re.compile(r"\.icims\.com", re.I),
    "taleo": re.compile(r"taleo\.net", re.I),
    "oracle_hcm": re.compile(r"oraclecloud\.com/hcmUI", re.I),
    "teamtailor": re.compile(r"\.teamtailor\.com", re.I),
    "join": re.compile(r"join\.com/companies", re.I),
    "jobvite": re.compile(r"jobs\.jobvite\.com", re.I),
    "pinpoint": re.compile(r"pinpointhq\.com", re.I),
    "bamboohr": re.compile(r"\.bamboohr\.com", re.I),
}
IGNORE_SLUGS = {"embed", "v1", "boards", "js", "api", "www", "jobs", "careers", "job_board"}


@dataclass
class Resolution:
    company: Company
    hit: ProbeResult | None = None
    method: str = ""
    maybes: list[ProbeResult] = field(default_factory=list)
    detected_unsupported: list[str] = field(default_factory=list)
    sample: list[str] = field(default_factory=list)
    error: str = ""


async def _fingerprint(client, c: Company, res: Resolution) -> ProbeResult | None:
    if not c.careers_url:
        return None
    try:
        r = await request(client, "GET", c.careers_url, retries=1)
        html = r.text + " " + str(r.url)
    except Exception as e:  # noqa: BLE001
        res.error = f"careers_url: {type(e).__name__}"
        return None
    wd = workday.config_from_url(html)
    if wd:
        hit = await workday.probe_config(client, wd)
        if hit:
            return hit
    ohcm = oracle_hcm.config_from_url(html)
    if ohcm:
        hit = await oracle_hcm.probe_config(client, ohcm)
        if hit:
            return hit
    if "teamtailor" in html.lower():  # <x>.teamtailor.com links, or a custom domain served by Teamtailor
        for host in dict.fromkeys([*teamtailor.HOST_RE.findall(html), urlparse(str(r.url)).netloc]):
            hit = await teamtailor.probe_host(client, host)
            if hit and hit.jobs:
                return hit
    if successfactors.RMK_RE.search(html):
        hit = await successfactors.probe_url(client, str(r.url))
        if hit:
            return hit
    for ats, rx in FINGERPRINTS:
        for slug in dict.fromkeys(m.group(1) for m in rx.finditer(html)):
            if slug.lower() in IGNORE_SLUGS:
                continue
            hit = await ADAPTERS[ats].probe(client, slug)
            if hit:
                return hit
    res.detected_unsupported = [k for k, rx in UNSUPPORTED.items() if rx.search(html)]
    return None


async def _slug_probe(client, c: Company, res: Resolution, max_candidates: int) -> ProbeResult | None:
    for slug in slug_candidates(c)[:max_candidates]:
        for mod in PROBE_ORDER:
            try:
                hit = await mod.probe(client, slug)
            except Exception:  # noqa: BLE001
                hit = None
            if hit and hit.jobs > 0:
                return hit
            if hit:
                res.maybes.append(hit)
    return None


async def _sample(client, c: Company, hit: ProbeResult) -> list[str]:
    tmp = Company(key=c.key, name=c.name, ats=hit.ats, slug=hit.slug, config=hit.config or {})
    try:
        posts = await ADAPTERS[hit.ats].fetch(client, tmp, FetchContext(search_terms=["intern"], max_pages=1))
    except Exception as e:  # noqa: BLE001
        return [f"(sample failed: {type(e).__name__})"]
    return [f"{p.title} — {p.location}" for p in posts[:4]]


async def resolve_one(client, sem, c: Company, max_candidates: int) -> Resolution:
    res = Resolution(company=c)
    async with sem:
        hit = await _fingerprint(client, c, res)
        if hit:
            res.method = "careers_page"
        else:
            hit = await _slug_probe(client, c, res, max_candidates)
            res.method = "slug_probe" if hit else ""
        if hit:
            res.hit = hit
            res.sample = await _sample(client, c, hit)
    return res


async def discover(companies: list[Company], concurrency: int = 6, max_candidates: int = 4) -> list[Resolution]:
    sem = asyncio.Semaphore(concurrency)
    async with make_client(timeout=15) as client:
        return await asyncio.gather(*(resolve_one(client, sem, c, max_candidates) for c in companies))


def apply(resolutions: list[Resolution]) -> None:
    today = date.today().isoformat()
    for r in resolutions:
        c = r.company
        if r.hit:
            c.ats, c.slug, c.config = r.hit.ats, r.hit.slug, (r.hit.config or {})
            c.notes = f"auto {today} via {r.method}: {r.hit.jobs} jobs — CHECK sample"
        elif r.detected_unsupported:
            c.notes = f"auto {today}: detected {', '.join(r.detected_unsupported)} (adapter in S2)"


def report(resolutions: list[Resolution]) -> str:
    lines = ["| Company | Result | ATS/slug | Jobs | Sample / notes |", "|---|---|---|---|---|"]
    ok = 0
    for r in sorted(resolutions, key=lambda x: (x.hit is None, x.company.tier, x.company.name)):
        c = r.company
        if r.hit:
            ok += 1
            lines.append(f"| {c.name} | ✅ {r.method} | {r.hit.ats}/{r.hit.slug} | {r.hit.jobs} | {'; '.join(r.sample[:2])} |")
        else:
            extra = ", ".join(r.detected_unsupported) or ", ".join(f"{m.ats}/{m.slug}(0)" for m in r.maybes[:3]) or r.error
            lines.append(f"| {c.name} | ❌ | — | — | {extra} |")
    lines.insert(0, f"Resolved {ok}/{len(resolutions)} companies.\n")
    return "\n".join(lines)
