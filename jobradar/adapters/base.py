"""Adapter protocol.

Every adapter module exposes:
  NAME: str
  async fetch(client, company, ctx) -> list[Posting]     # full board for this company
  parse(payload, company) -> list[Posting]               # pure, unit-tested against fixtures
  async probe(client, slug) -> ProbeResult | None        # used by `discover` (None = no board)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProbeResult:
    ats: str
    slug: str
    jobs: int
    board_name: str = ""          # if the API tells us the org name — used to validate matches
    config: dict | None = None


@dataclass
class FetchContext:
    """Run-level knobs adapters may need (e.g. search terms for query-based ATSs)."""
    search_terms: list[str]
    max_pages: int = 10
