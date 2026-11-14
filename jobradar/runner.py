"""One pipeline run: fetch → persist → filter → notify → health."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db, health
from .adapters import ADAPTERS
from .adapters.base import FetchContext
from .config import Settings, load_companies
from .filters import RulesFilter
from .http import make_client
from .models import Company, Posting
from .notify import Telegram, format_posting, hc_ping
from .scoring import score_pending

log = logging.getLogger(__name__)
ANOMALY_MIN_PREV = 5   # alert when a board that had >= this many postings suddenly returns 0


@dataclass
class FetchResult:
    company: Company
    postings: list[Posting] | None
    error: str | None
    ms: int
    reason: str | None = None        # short human reason (health.reason)
    fingerprint: str | None = None   # stable problem identity (health.fingerprint)


async def _fetch_one(client, sem, c: Company, ctx: FetchContext) -> FetchResult:
    async with sem:
        t0 = time.monotonic()
        try:
            posts = await ADAPTERS[c.ats].fetch(client, c, ctx)
            return FetchResult(c, posts, None, int((time.monotonic() - t0) * 1000))
        except Exception as e:  # noqa: BLE001 — one broken board must never kill the run
            log.warning("fetch failed for %s (%s): %r", c.name, c.ats, e)
            return FetchResult(c, None, f"{type(e).__name__}: {e}", int((time.monotonic() - t0) * 1000),
                               health.reason(e), health.fingerprint(c.key, c.ats, e))


async def fetch_all(companies: list[Company], ctx: FetchContext, concurrency: int) -> list[FetchResult]:
    sem = asyncio.Semaphore(concurrency)
    async with make_client() as client:
        return await asyncio.gather(*(_fetch_one(client, sem, c, ctx) for c in companies))


def active_companies(companies: list[Company], only: list[str] | None = None) -> list[Company]:
    out = [c for c in companies if c.enabled and c.ats in ADAPTERS and (c.slug or c.ats == "amazon" or c.config)]
    if only:
        wanted = {o.lower() for o in only}
        out = [c for c in out if c.key in wanted or c.name.lower() in wanted]
    return out


def dry_run(settings: Settings, only: list[str] | None = None, show_rejects: bool = False) -> list[tuple]:
    """Fetch + filter without a database; prints matches. Great for tuning filters.yaml."""
    rf = RulesFilter.from_file(settings.filters_file)
    comps = active_companies(load_companies(settings.companies_file), only)
    ctx = FetchContext(search_terms=rf.search_terms)
    results = asyncio.run(fetch_all(comps, ctx, settings.concurrency))
    rows = []
    for fr in results:
        if fr.postings is None:
            print(f"!! {fr.company.name}: {fr.reason}  [{fr.fingerprint}]")
            continue
        n_pass = 0
        for p in fr.postings:
            r = rf.evaluate(p)
            if r.passed:
                n_pass += 1
                rows.append((fr.company.tier, fr.company.name, p.title, r.location, ",".join(r.windows), r.level, p.url))
            elif show_rejects:
                print(f"   - {fr.company.name}: {p.title} [{p.location}] -> {r.reasons}")
        print(f"{fr.company.name:<22} {fr.company.ats:<15} {len(fr.postings):>5} postings  {n_pass:>3} match  {fr.ms}ms")
    print()
    for r in sorted(rows):
        print(" | ".join(str(x) for x in r))
    return rows


def run(settings: Settings, only: list[str] | None = None) -> dict:
    if not settings.database_url:
        raise SystemExit("DATABASE_URL not set (use `dry-run` to test without Postgres)")
    rf = RulesFilter.from_file(settings.filters_file)
    all_companies = load_companies(settings.companies_file)
    comps = active_companies(all_companies, only)
    tg = Telegram(settings.telegram_token, settings.telegram_chat_id)
    hc_ping(settings.hc_ping_url, "/start")

    conn = db.connect(settings.database_url)
    db.migrate(conn)
    db.upsert_companies(conn, all_companies)
    rid = db.start_run(conn)
    stats = dict(companies_ok=0, companies_failed=0, postings_seen=0, new_postings=0, new_matches=0)
    failures, anomalies, baseline_hits = [], [], []
    events: list[health.Event] = []

    try:
        results = asyncio.run(fetch_all(comps, FetchContext(search_terms=rf.search_terms), settings.concurrency))
        for fr in results:
            c = fr.company
            label = f"{c.name} ({c.ats})"
            if fr.postings is None:
                stats["companies_failed"] += 1
                failures.append(f"{label}: {fr.reason}")
                events.append(health.Event(c.key, label, False, fr.fingerprint, fr.reason))
                db.record_company_run(conn, rid, c.key, False, None, fr.error, fr.ms)
                conn.commit()
                continue
            baseline = db.has_baseline(conn, c.key)
            prev = db.previous_count(conn, c.key)
            n = len(fr.postings)
            if n == 0 and prev and prev >= ANOMALY_MIN_PREV:
                # Suspicious empty board: don't close everything, flag it instead.
                anomalies.append(f"{label}: 0 postings (was {prev})")
                events.append(health.Event(c.key, label, False, health.ANOMALY_FP, f"0 postings (was {prev})"))
                db.record_company_run(conn, rid, c.key, False, 0, "anomaly: empty board", fr.ms)
                conn.commit()
                continue
            events.append(health.Event(c.key, label, True))
            new = db.sync_postings(conn, c, fr.postings)
            for p in new:
                r = rf.evaluate(p)
                # First-ever fetch of a company = baseline: store matches silently, summarise once.
                db.save_match(conn, p.uid, r, notified=not baseline)
                if r.passed:
                    stats["new_matches"] += 1
                    if not baseline:
                        baseline_hits.append({"company": c.name, "tier": c.tier, "title": p.title, "url": p.url,
                                              "location": r.location, "windows": r.windows, "level": r.level})
            db.record_company_run(conn, rid, c.key, True, n, None, fr.ms)
            conn.commit()
            stats["companies_ok"] += 1
            stats["postings_seen"] += n
            stats["new_postings"] += len(new)

        # ---- stage 2: LLM-score new matches before deciding how to notify them ----
        try:
            scored = score_pending(conn, settings)
            stats_note = f"scored {scored.get('scored', 0)}, failed {scored.get('failed', 0)}"
            log.info("llm scoring: %s", scored)
        except Exception as e:  # noqa: BLE001 — scoring is best-effort; unscored matches fall back to tier rules
            conn.rollback()
            stats_note = f"scoring error: {e!r}"[:200]
            log.warning("llm scoring failed: %r", e)

        # ---- notifications: instant (tier A or score >= INSTANT_SCORE) every run, the rest as a daily digest ----
        rule = {"instant_tiers": list(settings.instant_tiers), "instant_score": settings.instant_score}
        pending = db.pending_notifications(conn, limit=settings.notify_max + 1, instant=True, **rule)
        shown = pending[: settings.notify_max]
        if shown:
            more = len(pending) - len(shown)
            header = f"<b>Job Radar</b> — {len(shown)} new match{'es' if len(shown) != 1 else ''}"
            blocks = [format_posting(m) for m in shown]
            if more > 0:
                blocks.append("…and more. Run <code>jobradar report</code> for the full list.")
            if tg.send_many(header, blocks):
                db.mark_notified(conn, [m["uid"] for m in shown])
        if (settings.digest_hour is not None and datetime.now(ZoneInfo(settings.tz)).hour >= settings.digest_hour
                and not db.digest_sent_today(conn, settings.tz)):
            digest = db.pending_notifications(conn, limit=None, instant=False, **rule)
            shown = digest[: settings.notify_max]
            blocks = [format_posting(m) for m in shown]
            if len(digest) > len(shown):
                blocks.append(f"…and {len(digest) - len(shown)} more on the Grafana Job Radar dashboard.")
            header = (f"<b>Job Radar daily digest</b> — {len(digest)} new tier "
                      f"{'/'.join(sorted({m['tier'] for m in digest}))} match{'es' if len(digest) != 1 else ''}")
            # Everything in the digest counts as delivered (the overflow lives on the dashboard), so
            # tomorrow's digest only carries tomorrow's news. An empty digest still closes the day.
            if not digest or tg.send_many(header, blocks):
                db.mark_notified(conn, [m["uid"] for m in digest])
                db.mark_digest_sent(conn, rid)
        if baseline_hits:
            baseline_hits.sort(key=lambda m: (m["tier"], m["level"] != "strong", m["company"]))
            header = (f"<b>Job Radar baseline</b> — {len(baseline_hits)} open matching roles at newly tracked companies "
                      f"(showing {min(15, len(baseline_hits))})")
            tg.send_many(header, [format_posting(m) for m in baseline_hits[:15]])
        report_health(conn, tg, events, settings.health_reminder_days)

        status = "ok" if not failures and not anomalies else ("partial" if stats["companies_ok"] else "failed")
        db.finish_run(conn, rid, status=status, notes=stats_note, **stats)
        summary = f"{status}: {stats}"
        log.info(summary)
        hc_ping(settings.hc_ping_url, "" if status != "failed" else "/fail", summary)
        return {"status": status, **stats, "failures": failures, "anomalies": anomalies}
    except Exception as e:
        conn.rollback()
        db.finish_run(conn, rid, status="failed", notes=repr(e)[:500], **stats)
        hc_ping(settings.hc_ping_url, "/fail", repr(e))
        raise
    finally:
        conn.close()


def report_health(conn, tg: Telegram, events: list[health.Event], reminder_days: int = 0) -> list[str]:
    """One health message per run with only what changed (new / changed / reminder / recovered); silent otherwise.
    Returns the blocks sent (empty if none)."""
    from datetime import timezone
    now = datetime.now(timezone.utc)
    prev = db.load_health(conn)
    decided = [(ev, health.decide(prev.get(ev.company_key), ev, now, reminder_days)) for ev in events]
    blocks = health.build_message(decided)
    sent = bool(blocks) and tg.send_many("<b>Job Radar health</b>", blocks)
    rows = []
    for ev, d in decided:
        if d.kind == "recovered" and not sent:
            continue                    # keep it 'failing' so the recovery is announced next run
        if d.kind in ("new", "changed", "reminder") and sent:
            d.state.notified_at = now
        if d.kind is not None or ev.company_key in prev or not ev.ok:
            rows.append(d.state)
    db.save_health(conn, rows)
    return blocks if sent else []


def refilter(settings: Settings, silent: bool = True) -> int:
    """Re-evaluate all open postings after editing filters.yaml."""
    rf = RulesFilter.from_file(settings.filters_file)
    conn = db.connect(settings.database_url)
    n = 0
    for uid, p in db.open_postings(conn):
        r = rf.evaluate(p)
        db.save_match(conn, uid, r, notified=silent)
        n += r.passed
    if silent:
        conn.execute("UPDATE matches SET notified_at = COALESCE(notified_at, now()) WHERE passed")
    conn.commit()
    conn.close()
    return n
