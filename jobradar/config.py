"""Settings (env) + company registry loading."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import Company

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    database_url: str | None
    telegram_token: str | None
    telegram_chat_id: str | None
    hc_ping_url: str | None
    companies_file: Path
    filters_file: Path
    concurrency: int
    notify_max: int
    instant_tiers: tuple[str, ...] = ("A",)   # tiers alerted on every run; the rest go into the daily digest
    digest_hour: int | None = 9               # local hour after which the daily digest goes out; None = no digest
    tz: str = "Europe/Lisbon"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    instant_score: int | None = 75             # LLM score that makes any tier instant; None = tier rule only
    score_max_per_run: int = 150               # cap on LLM calls per run (backfill with `score`)
    score_concurrency: int = 4
    profile_file: Path = ROOT / "config" / "profile.yaml"
    tier_weights: dict = field(default_factory=lambda: {"A": 1.5, "B": 1.0, "C": 0.66})  # llm_score = raw × weight
    health_reminder_days: int = 0              # >0: re-announce still-failing boards once every N days

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            database_url=e("DATABASE_URL"),
            telegram_token=e("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=e("TELEGRAM_CHAT_ID"),
            hc_ping_url=e("HC_PING_URL"),
            companies_file=Path(e("COMPANIES_FILE", ROOT / "config" / "companies.yaml")),
            filters_file=Path(e("FILTERS_FILE", ROOT / "config" / "filters.yaml")),
            concurrency=int(e("JOBRADAR_CONCURRENCY", "8")),
            notify_max=int(e("NOTIFY_MAX", "30")),
            instant_tiers=tuple(t.strip() for t in e("INSTANT_TIERS", "A").split(",") if t.strip()),
            digest_hour=int(e("DIGEST_HOUR", "9")) if e("DIGEST_HOUR", "9").strip() else None,
            tz=e("TZ", "Europe/Lisbon"),
            gemini_api_key=e("GEMINI_API_KEY") or None,
            gemini_model=e("GEMINI_MODEL", "gemini-2.5-flash"),
            instant_score=int(e("INSTANT_SCORE", "75")) if e("INSTANT_SCORE", "75").strip() else None,
            score_max_per_run=int(e("SCORE_MAX_PER_RUN", "150")),
            score_concurrency=int(e("SCORE_CONCURRENCY", "4")),
            profile_file=Path(e("PROFILE_FILE", ROOT / "config" / "profile.yaml")),
            tier_weights=parse_weights(e("TIER_WEIGHTS", "A:1.5,B:1,C:0.66")),
            health_reminder_days=int(e("HEALTH_REMINDER_DAYS", "0") or 0),
        )


def parse_weights(spec: str) -> dict[str, float]:
    """"A:1.5,B:1,C:0.66" -> {"A": 1.5, "B": 1.0, "C": 0.66}; tiers not listed get weight 1."""
    out = {}
    for part in (spec or "").split(","):
        if ":" in part:
            tier, w = part.split(":", 1)
            out[tier.strip()] = float(w)
    return out


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def slug_candidates(c: Company) -> list[str]:
    """Explicit candidates first, then mechanical variants of the name."""
    base = re.sub(r"\(.*?\)", "", c.name).lower().strip()
    base = re.sub(r"\.(com|pro|io|ai)$", "", base)
    words = re.findall(r"[a-z0-9]+", base)
    variants = ["".join(words), "-".join(words)]  # never the bare first word of a multi-word name (collisions)
    for suffix in ("hq", "inc", "jobs", "careers"):
        variants.append("".join(words) + suffix)
    seen, out = set(), []
    for v in [*c.candidates, *variants]:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def load_companies(path: Path) -> list[Company]:
    data = yaml.safe_load(Path(path).read_text()) or {}
    out = []
    for row in data.get("companies", []):
        row = dict(row)
        name = row.pop("name")
        key = row.pop("key", None) or slugify(name)
        out.append(Company(key=key, name=name, **row))
    return out


def save_companies(path: Path, companies: list[Company], header: str = "") -> None:
    rows = []
    for c in companies:
        row = {"name": c.name, "key": c.key, "tier": c.tier, "bucket": c.bucket}
        for k in ("ats", "slug", "careers_url", "notes"):
            if getattr(c, k):
                row[k] = getattr(c, k)
        if c.config:
            row["config"] = c.config
        if c.candidates:
            row["candidates"] = c.candidates
        if not c.enabled:
            row["enabled"] = False
        rows.append(row)
    text = yaml.safe_dump({"companies": rows}, sort_keys=False, allow_unicode=True, width=140)
    Path(path).write_text(header + text)
