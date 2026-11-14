"""CLI: python -m jobradar <command>

  discover [--write] [--all] [--only NAME ...]   resolve ATS per company (writes config/companies.yaml with --write)
  dry-run  [--only NAME ...] [--rejects]         fetch + filter, print matches, no DB
  run      [--only NAME ...]                      full pipeline into Postgres + Telegram
  loop     --every 4h                             run forever on an interval (Docker service mode)
  report   [--tier A] [--send]                    list open matches (optionally push to Telegram)
  refilter [--notify]                             re-apply filters.yaml to all open postings
  score    [--limit N] [--rescore]                LLM-score passed open matches (backfill; --rescore after editing profile.yaml)
  migrate                                         create/upgrade schema only
  health                                          currently failing (and muted) boards
  mute KEY / unmute KEY                           silence / re-enable health alerts for one company
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time
from datetime import date

from . import db
from .config import Settings, load_companies, save_companies
from .notify import Telegram, format_posting


def _interval(s: str) -> int:
    m = re.fullmatch(r"(\d+)([smh])", s.strip())
    if not m:
        raise argparse.ArgumentTypeError("use e.g. 30m, 4h")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jobradar")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover"); d.add_argument("--write", action="store_true"); d.add_argument("--all", action="store_true")
    d.add_argument("--only", nargs="*"); d.add_argument("--candidates", type=int, default=4)
    r = sub.add_parser("dry-run"); r.add_argument("--only", nargs="*"); r.add_argument("--rejects", action="store_true")
    r2 = sub.add_parser("run"); r2.add_argument("--only", nargs="*")
    lp = sub.add_parser("loop"); lp.add_argument("--every", type=_interval, default=_interval("4h"))
    rp = sub.add_parser("report"); rp.add_argument("--tier"); rp.add_argument("--send", action="store_true")
    rf = sub.add_parser("refilter"); rf.add_argument("--notify", action="store_true")
    sc = sub.add_parser("score"); sc.add_argument("--limit", type=int, default=2000); sc.add_argument("--rescore", action="store_true")
    sub.add_parser("migrate")
    sub.add_parser("health")
    for name in ("mute", "unmute"):
        sub.add_parser(name).add_argument("key")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # INFO request lines include the Telegram bot token in the URL
    s = Settings.from_env()

    if a.cmd == "discover":
        from .discover import apply, discover, report
        comps = load_companies(s.companies_file)
        targets = [c for c in comps if (a.all or not c.ats) and c.enabled]
        if a.only:
            wanted = {o.lower() for o in a.only}
            targets = [c for c in targets if c.key in wanted or c.name.lower() in wanted]
        print(f"Discovering {len(targets)} companies…", file=sys.stderr)
        res = asyncio.run(discover(targets, max_candidates=a.candidates))
        rep = report(res)
        print(rep)
        out = s.companies_file.parent / f"discover_report_{date.today().isoformat()}.md"
        out.write_text(rep)
        if a.write:
            apply(res)
            backup = s.companies_file.with_suffix(".yaml.bak")
            backup.write_text(s.companies_file.read_text())
            header = s.companies_file.read_text().split("companies:")[0]
            save_companies(s.companies_file, comps, header=header)
            print(f"\nWrote {s.companies_file} (backup: {backup.name}). Review the ✅ rows' samples!", file=sys.stderr)
        return 0

    if a.cmd == "dry-run":
        from .runner import dry_run
        dry_run(s, a.only, a.rejects)
        return 0

    if a.cmd == "run":
        from .runner import run
        res = run(s, a.only)
        return 0 if res["status"] != "failed" else 1

    if a.cmd == "loop":
        from .runner import run
        while True:
            try:
                run(s)
            except Exception:  # noqa: BLE001
                logging.exception("run failed")
            time.sleep(a.every)

    if a.cmd == "migrate":
        conn = db.connect(s.database_url); db.migrate(conn); conn.close()
        print("schema jobradar ready")
        return 0

    if a.cmd == "health":
        conn = db.connect(s.database_url); db.migrate(conn)
        rows = db.health_rows(conn)
        conn.close()
        print(f"{'company':<18} {'status':<8} {'since':<12} {'runs':>4}  {'muted':<5}  {'fingerprint':<44} reason")
        for h in rows:
            since = f"{h['since']:%Y-%m-%d}" if h["since"] else "—"
            print(f"{h['key']:<18} {h['status']:<8} {since:<12} {h['consecutive']:>4}  {'yes' if h['muted'] else '':<5}  "
                  f"{h['fingerprint'] or '—':<44} {h['reason'] or ''}")
        n_fail = sum(h["status"] == "failing" for h in rows)
        print(f"\n{n_fail} failing, {sum(h['muted'] for h in rows)} muted")
        return 0

    if a.cmd in ("mute", "unmute"):
        conn = db.connect(s.database_url); db.migrate(conn)
        ok = db.set_muted(conn, a.key, a.cmd == "mute")
        conn.close()
        print(f"{a.key}: {a.cmd}d" if ok else f"unknown company key: {a.key}")
        return 0 if ok else 1

    if a.cmd == "report":
        conn = db.connect(s.database_url)
        rows = db.open_matches(conn, a.tier)
        conn.close()
        for m in rows:
            print(f"[{m['tier']}] {m['company']:<20} {m['title'][:70]:<70} {m['location'] or '':<14} {','.join(m['windows'])}  {m['url']}")
        print(f"\n{len(rows)} open matches")
        if a.send and rows:
            Telegram(s.telegram_token, s.telegram_chat_id).send_many(
                f"<b>Job Radar report</b> — {len(rows)} open matches", [format_posting(m) for m in rows[:40]])
        return 0

    if a.cmd == "refilter":
        from .runner import refilter
        print(f"{refilter(s, silent=not a.notify)} postings pass the current filters")
        return 0

    if a.cmd == "score":
        from .scoring import score_pending
        conn = db.connect(s.database_url)
        db.migrate(conn)
        print(score_pending(conn, s, limit=a.limit, rescore=a.rescore))
        conn.close()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
