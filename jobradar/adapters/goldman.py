"""Goldman Sachs campus roles (higher.gs.com) — custom site; its GraphQL gateway is public:
  POST https://api-higher.gs.com/gateway/api/v1/graphql   operation GetCampusRoles, experiences ["CAMPUS"]
Role pages live at https://higher.gs.com/roles/<externalSource.sourceId>.
"""
from __future__ import annotations

from ..http import request
from ..models import Company, Posting
from .base import FetchContext, ProbeResult

NAME = "goldman"
PAGE = 100
API = "https://api-higher.gs.com/gateway/api/v1/graphql"
QUERY = """query GetCampusRoles($searchQueryInput: RoleSearchQueryInput!) {
  roleSearch(searchQueryInput: $searchQueryInput) {
    totalCount
    items { roleId jobTitle jobFunction division locations { primary state country city }
            jobType { code description } externalSource { sourceId } educationLevel }
  }
}"""


def parse(payload: dict, company: Company) -> list[Posting]:
    out = []
    for r in ((payload.get("data") or {}).get("roleSearch") or {}).get("items") or []:
        source_id = (r.get("externalSource") or {}).get("sourceId") or r["roleId"]
        locs = r.get("locations") or []
        primary = next((l for l in locs if l.get("primary")), locs[0] if locs else {})
        out.append(Posting(
            company_key=company.key, source=NAME, external_id=str(r["roleId"]), title=(r.get("jobTitle") or "").strip(),
            url=f"https://higher.gs.com/roles/{source_id}",
            location=" | ".join(", ".join(x for x in (l.get("city"), l.get("country")) if x) for l in locs),
            city=primary.get("city") or "", country=primary.get("country") or "",
            department=r.get("division") or r.get("jobFunction") or "",
            raw={k: r.get(k) for k in ("roleId", "jobType", "educationLevel")},
        ))
    return out


async def fetch(client, company: Company, ctx: FetchContext) -> list[Posting]:
    seen: dict[str, Posting] = {}
    for page in range(20):
        body = {"operationName": "GetCampusRoles", "query": QUERY, "variables": {"searchQueryInput": {
            "page": {"pageSize": PAGE, "pageNumber": page}, "filters": [], "experiences": ["CAMPUS"], "searchTerm": ""}}}
        payload = (await request(client, "POST", API, json=body,
                                 headers={"Origin": "https://higher.gs.com", "Referer": "https://higher.gs.com/"})).json()
        if payload.get("errors"):
            raise RuntimeError(f"goldman graphql: {payload['errors'][0].get('message', '')[:120]}")
        batch = parse(payload, company)
        for p in batch:
            seen.setdefault(p.external_id, p)
        total = int(((payload.get("data") or {}).get("roleSearch") or {}).get("totalCount") or 0)
        if len(batch) < PAGE or (page + 1) * PAGE >= total:
            break
    return list(seen.values())


async def probe(client, slug: str) -> ProbeResult | None:  # single-company adapter
    return None
