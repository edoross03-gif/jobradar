"""Personio public XML feed: https://{slug}.jobs.personio.de/xml (also .com)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from ..http import request
from ..models import Company, Posting, parse_ts, strip_html
from .base import FetchContext, ProbeResult

NAME = "personio"
DOMAINS = ("jobs.personio.de", "jobs.personio.com")


def _t(el, tag):
    x = el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def parse(xml_text: str, company: Company) -> list[Posting]:
    root = ET.fromstring(xml_text)
    domain = company.config.get("domain", DOMAINS[0])
    out = []
    for p in root.iter("position"):
        pid = _t(p, "id")
        offices = [_t(p, "office")] + [(o.text or "").strip() for o in p.findall("additionalOffices/office")]
        desc = " ".join(_t(d, "value") for d in p.findall("jobDescriptions/jobDescription"))
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=pid,
            title=_t(p, "name"), url=f"https://{company.slug}.{domain}/job/{pid}",
            location="; ".join(o for o in offices if o), department=_t(p, "department"),
            employment_type=" / ".join(x for x in (_t(p, "employmentType"), _t(p, "seniority"), _t(p, "schedule")) if x),
            posted_at=parse_ts(_t(p, "createdAt")), description=strip_html(desc),
            raw={"id": pid, "name": _t(p, "name"), "office": _t(p, "office"), "employmentType": _t(p, "employmentType")},
        ))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    domain = company.config.get("domain", DOMAINS[0])
    r = await request(client, "GET", f"https://{company.slug}.{domain}/xml", params={"language": "en"})
    return parse(r.text, company)


async def probe(client, slug: str) -> ProbeResult | None:
    for domain in DOMAINS:
        try:
            r = await request(client, "GET", f"https://{slug}.{domain}/xml", retries=1)
        except Exception:
            continue
        if "<workzag-jobs" not in r.text[:500]:
            continue
        n = r.text.count("<position>")
        return ProbeResult(NAME, slug, n, config={"domain": domain})
    return None
