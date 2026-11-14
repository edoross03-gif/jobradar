"""Core data types."""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str | None, limit: int = 6000) -> str:
    """HTML (possibly entity-escaped, as Greenhouse returns it) -> compact plain text."""
    if not text:
        return ""
    text = html.unescape(html.unescape(text))
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()[:limit]


def parse_ts(value: Any) -> datetime | None:
    """Accept ISO strings, 'YYYY-MM-DD', or epoch millis/seconds."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)):
            v = float(value)
            if v > 1e12:  # millis
                v /= 1000
            return datetime.fromtimestamp(v, tz=timezone.utc)
        s = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


@dataclass
class Company:
    key: str                      # stable id, e.g. "trade-republic"
    name: str
    tier: str = "B"               # A / B / C
    bucket: str = ""
    ats: str | None = None        # greenhouse | lever | ashby | ... | None (unresolved)
    slug: str | None = None       # board token / account slug
    config: dict = field(default_factory=dict)   # adapter-specific (workday host/site, lever region...)
    careers_url: str | None = None
    candidates: list[str] = field(default_factory=list)
    notes: str = ""
    enabled: bool = True


@dataclass
class Posting:
    company_key: str
    source: str                   # adapter name
    external_id: str
    title: str
    url: str
    location: str = ""            # human-readable, may list several
    city: str = ""
    country: str = ""
    remote: bool | None = None
    department: str = ""
    employment_type: str = ""     # "Intern", "Full-time", ...
    posted_at: datetime | None = None
    description: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def uid(self) -> str:
        """Stable primary key across runs."""
        return hashlib.sha1(f"{self.source}:{self.company_key}:{self.external_id}".encode()).hexdigest()

    def haystack_location(self) -> str:
        return " | ".join(x for x in (self.location, self.city, self.country) if x)
