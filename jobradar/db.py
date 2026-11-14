"""Postgres persistence (psycopg 3). All objects live in schema `jobradar`."""
from __future__ import annotations

import json
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from .filters import FilterResult
from .models import Company, Posting

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def connect(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn, autocommit=False)
    conn.execute("SET search_path TO jobradar, public")
    return conn


def migrate(conn: psycopg.Connection) -> None:
    for f in sorted(SQL_DIR.glob("*.sql")):
        conn.execute(f.read_text())
    conn.commit()


def upsert_companies(conn, companies: list[Company]) -> None:
    with conn.cursor() as cur:
        for c in companies:
            cur.execute(
                """INSERT INTO companies(key, name, tier, bucket, ats, slug, config, careers_url, enabled, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                   ON CONFLICT (key) DO UPDATE SET name=EXCLUDED.name, tier=EXCLUDED.tier, bucket=EXCLUDED.bucket,
                     ats=EXCLUDED.ats, slug=EXCLUDED.slug, config=EXCLUDED.config, careers_url=EXCLUDED.careers_url,
                     enabled=EXCLUDED.enabled, updated_at=now()""",
                (c.key, c.name, c.tier, c.bucket, c.ats, c.slug, Jsonb(c.config), c.careers_url, c.enabled),
            )
    conn.commit()


def start_run(conn) -> int:
    rid = conn.execute("INSERT INTO runs DEFAULT VALUES RETURNING id").fetchone()[0]
    conn.commit()
    return rid


def finish_run(conn, rid: int, **stats) -> None:
    status = stats.pop("status")
    sets = ", ".join(f"{k}=%s" for k in stats)
    conn.execute(f"UPDATE runs SET finished_at=now(), status=%s, {sets} WHERE id=%s", (status, *stats.values(), rid))
    conn.commit()


def record_company_run(conn, rid: int, key: str, ok: bool, n: int | None, error: str | None, ms: int) -> None:
    conn.execute(
        "INSERT INTO company_runs(run_id, company_key, ok, n_postings, error, duration_ms) VALUES (%s,%s,%s,%s,%s,%s)",
        (rid, key, ok, n, (error or "")[:1000] or None, ms),
    )


def has_baseline(conn, key: str) -> bool:
    """True if this company already had at least one successful fetch (so new postings are really new)."""
    row = conn.execute(
        "SELECT 1 FROM company_runs WHERE company_key=%s AND ok LIMIT 1", (key,)).fetchone()
    return row is not None


def previous_count(conn, key: str) -> int | None:
    row = conn.execute(
        """SELECT max(n_postings) FROM (SELECT n_postings FROM company_runs WHERE company_key=%s AND ok
           ORDER BY run_id DESC LIMIT 5) t""", (key,)).fetchone()
    return row[0] if row else None


def sync_postings(conn, company: Company, postings: list[Posting]) -> list[Posting]:
    """Upsert a *complete* successful fetch for one company. Returns postings that are new.

    Postings of this company (same source) not present in the fetch are marked closed; ones that reappear reopen.
    """
    sources = sorted({company.ats, *(p.source for p in postings)} - {None})
    uids = [p.uid for p in postings]
    existing = {r[0] for r in conn.execute(
        "SELECT uid FROM postings WHERE uid = ANY(%s)", (uids,)).fetchall()} if uids else set()
    new = []
    with conn.cursor() as cur:
        for p in postings:
            if p.uid in existing:
                cur.execute(
                    """UPDATE postings SET last_seen=now(), closed_at=NULL, title=%s, url=%s, location=%s,
                       description=COALESCE(NULLIF(%s,''), description) WHERE uid=%s""",
                    (p.title, p.url, p.location, p.description, p.uid))
            else:
                cur.execute(
                    """INSERT INTO postings(uid, company_key, source, external_id, title, url, location, city, country,
                         remote, department, employment_type, posted_at, description, raw)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (p.uid, p.company_key, p.source, p.external_id, p.title, p.url, p.location, p.city, p.country,
                     p.remote, p.department, p.employment_type, p.posted_at, p.description,
                     Jsonb(json.loads(json.dumps(p.raw, default=str)))))
                new.append(p)
        cur.execute(
            """UPDATE postings SET closed_at=now()
               WHERE company_key=%s AND source = ANY(%s) AND closed_at IS NULL AND NOT (uid = ANY(%s))""",
            (company.key, sources, uids))
    return new


def save_match(conn, uid: str, r: FilterResult, notified: bool = False) -> None:
    conn.execute(
        """INSERT INTO matches(uid, passed, level, location_label, windows, reasons, evaluated_at, notified_at)
           VALUES (%s,%s,%s,%s,%s,%s,now(), CASE WHEN %s THEN now() END)
           ON CONFLICT (uid) DO UPDATE SET passed=EXCLUDED.passed, level=EXCLUDED.level,
             location_label=EXCLUDED.location_label, windows=EXCLUDED.windows, reasons=EXCLUDED.reasons,
             evaluated_at=now()""",
        (uid, r.passed, r.level or None, r.location or None, r.windows, r.reasons, notified))


def pending_notifications(conn, limit: int | None = 50, instant: bool | None = None,
                          instant_tiers: list[str] | None = None, instant_score: int | None = None) -> list[dict]:
    """Un-notified passed open matches. A match is "instant" if its company tier is in `instant_tiers` or its LLM
    score is >= `instant_score`; instant=True/False selects that set or its complement, None selects all."""
    rows = conn.execute(
        """SELECT p.uid, c.name, c.tier, p.title, p.url, m.location_label, m.windows, m.level, p.posted_at,
                  m.llm_score, m.llm_json
           FROM matches m JOIN postings p USING (uid) JOIN companies c ON c.key = p.company_key
           WHERE m.passed AND m.notified_at IS NULL AND p.closed_at IS NULL
             AND (%(instant)s::boolean IS NULL OR %(instant)s = (
                   c.tier = ANY(COALESCE(%(tiers)s::text[], '{}'))
                   OR COALESCE(m.llm_score >= %(min_score)s::int, false)))
           ORDER BY m.llm_score DESC NULLS LAST, c.tier, m.level, p.first_seen LIMIT %(limit)s""",
        {"instant": instant, "tiers": instant_tiers, "min_score": instant_score, "limit": limit}).fetchall()
    cols = ["uid", "company", "tier", "title", "url", "location", "windows", "level", "posted_at", "llm_score",
            "llm_json"]
    return [dict(zip(cols, r)) for r in rows]


def unscored_matches(conn, limit: int | None = 150, rescore: bool = False) -> list[dict]:
    """Passed, open matches needing a score — not-yet-notified ones first (they may still earn an instant alert)."""
    rows = conn.execute(
        """SELECT m.uid, c.name, c.tier, c.bucket, p.title, p.location, m.location_label, p.employment_type,
                  p.department, m.windows, p.description
           FROM matches m JOIN postings p USING (uid) JOIN companies c ON c.key = p.company_key
           WHERE m.passed AND p.closed_at IS NULL AND (%(rescore)s OR m.scored_at IS NULL)
           ORDER BY (m.notified_at IS NULL) DESC, c.tier, p.first_seen DESC LIMIT %(limit)s""",
        {"rescore": rescore, "limit": limit}).fetchall()
    cols = ["uid", "company", "tier", "bucket", "title", "location", "location_label", "employment_type",
            "department", "windows", "description"]
    return [dict(zip(cols, r)) for r in rows]


def save_scores(conn, scored: list[tuple[str, dict]], weights: dict[str, float] | None = None) -> None:
    """llm_json keeps the model's raw verdict (llm_json.score); llm_score is the tier-weighted score."""
    with conn.cursor() as cur:
        for uid, res in scored:
            cur.execute("UPDATE matches SET llm_score=%s, llm_json=%s, scored_at=now() WHERE uid=%s",
                        (res["score"], Jsonb(res), uid))
    conn.commit()
    apply_tier_weights(conn, weights or {})


def apply_tier_weights(conn, weights: dict[str, float]) -> int:
    """Recompute llm_score = min(100, round(raw × weight of the company's current tier)) for every scored match.
    Pure SQL, no LLM calls — so changing a company's tier or TIER_WEIGHTS takes effect on the next run."""
    cur = conn.execute(
        """UPDATE matches m SET llm_score = LEAST(100, ROUND((m.llm_json->>'score')::numeric
                 * COALESCE((%(w)s::jsonb ->> c.tier)::numeric, 1)))::int
           FROM postings p JOIN companies c ON c.key = p.company_key
           WHERE p.uid = m.uid AND m.llm_json ? 'score'
             AND m.llm_score IS DISTINCT FROM LEAST(100, ROUND((m.llm_json->>'score')::numeric
                 * COALESCE((%(w)s::jsonb ->> c.tier)::numeric, 1)))::int""",
        {"w": json.dumps(weights)})
    conn.commit()
    return cur.rowcount


def mark_notified(conn, uids: list[str]) -> None:
    if uids:
        conn.execute("UPDATE matches SET notified_at=now() WHERE uid = ANY(%s)", (uids,))
        conn.commit()


def digest_sent_today(conn, tz: str) -> bool:
    row = conn.execute(
        """SELECT 1 FROM runs
           WHERE digest_sent_at >= (date_trunc('day', now() AT TIME ZONE %(tz)s) AT TIME ZONE %(tz)s) LIMIT 1""",
        {"tz": tz}).fetchone()
    return row is not None


def mark_digest_sent(conn, rid: int) -> None:
    conn.execute("UPDATE runs SET digest_sent_at=now() WHERE id=%s", (rid,))
    conn.commit()


_HEALTH_COLS = ["company_key", "status", "fingerprint", "reason", "first_failed_at", "last_failed_at",
                "consecutive_failures", "notified_at", "muted"]


def load_health(conn) -> dict:
    from .health import State
    rows = conn.execute(f"SELECT {', '.join(_HEALTH_COLS)} FROM health_state").fetchall()
    return {r[0]: State(*r) for r in rows}


def save_health(conn, states: list) -> None:
    """Upsert health rows. `muted` is never overwritten here — only `set_muted` (the CLI) changes it."""
    cols = _HEALTH_COLS[:-1]
    with conn.cursor() as cur:
        for s in states:
            cur.execute(
                f"""INSERT INTO health_state({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})
                    ON CONFLICT (company_key) DO UPDATE SET
                    {', '.join(f'{c}=EXCLUDED.{c}' for c in cols[1:])}""",
                tuple(getattr(s, c) for c in cols))
    conn.commit()


def set_muted(conn, key: str, muted: bool) -> bool:
    """Returns False if `key` is not a known company."""
    if not conn.execute("SELECT 1 FROM companies WHERE key=%s", (key,)).fetchone():
        return False
    conn.execute("""INSERT INTO health_state(company_key, muted) VALUES (%s, %s)
                    ON CONFLICT (company_key) DO UPDATE SET muted=EXCLUDED.muted""", (key, muted))
    conn.commit()
    return True


def health_rows(conn) -> list[dict]:
    """Currently failing boards, plus muted ones (so a mute is never forgotten)."""
    rows = conn.execute(
        """SELECT h.company_key, c.name, c.ats, h.status, h.fingerprint, h.reason, h.first_failed_at,
                  h.consecutive_failures, h.notified_at, h.muted
           FROM health_state h JOIN companies c ON c.key = h.company_key
           WHERE h.status = 'failing' OR h.muted
           ORDER BY h.status DESC, h.first_failed_at NULLS LAST, h.company_key""").fetchall()
    cols = ["key", "name", "ats", "status", "fingerprint", "reason", "since", "consecutive", "notified_at", "muted"]
    return [dict(zip(cols, r)) for r in rows]


def open_matches(conn, tier: str | None = None, limit: int = 200) -> list[dict]:
    q = "SELECT tier, company, title, location_label, windows, level, url, first_seen FROM v_open_matches"
    args: tuple = ()
    if tier:
        q += " WHERE tier = %s"
        args = (tier,)
    rows = conn.execute(q + " LIMIT %s", (*args, limit)).fetchall()
    cols = ["tier", "company", "title", "location", "windows", "level", "url", "first_seen"]
    return [dict(zip(cols, r)) for r in rows]


def open_postings(conn) -> list[tuple[str, Posting]]:
    rows = conn.execute(
        """SELECT uid, company_key, source, external_id, title, url, location, city, country, remote, department,
                  employment_type, posted_at, description FROM postings WHERE closed_at IS NULL""").fetchall()
    out = []
    for r in rows:
        p = Posting(company_key=r[1], source=r[2], external_id=r[3], title=r[4], url=r[5] or "", location=r[6] or "",
                    city=r[7] or "", country=r[8] or "", remote=r[9], department=r[10] or "",
                    employment_type=r[11] or "", posted_at=r[12], description=r[13] or "")
        out.append((r[0], p))
    return out
