"""Health alert de-duplication: unit tests for reasons/fingerprints + end-to-end runs (needs TEST_DATABASE_URL)."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg
import pytest
import respx

from jobradar import health
from jobradar.config import ROOT, Settings
from jobradar.http import NotFound
from jobradar.runner import run

DSN = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
FX = Path(__file__).parent / "fixtures"
REGISTRY = """companies:
- {name: Acme GH, key: acme-gh, tier: A, bucket: fintech, ats: greenhouse, slug: acmegh}
- {name: Broken Co, key: broken, tier: C, bucket: corp, ats: lever, slug: broken}
"""


def _status_error(code: int, url: str = "https://x.example/a/b?k=123") -> httpx.HTTPStatusError:
    req = httpx.Request("GET", url)
    return httpx.HTTPStatusError(f"Client error '{code}' for url '{url}'\nFor more information check: https://mdn/x",
                                 request=req, response=httpx.Response(code, request=req))


# ---------------------------------------------------------------- unit: reasons, fingerprints, shortening

def test_reasons_are_short_and_human():
    assert health.reason(NotFound("https://datapizza.jobs.personio.de/xml")) == "404 – board not found"
    assert health.reason(_status_error(403)) == "403 – blocked by site"
    assert health.reason(_status_error(503)) == "503 – server error"
    assert health.reason(json.JSONDecodeError("Expecting value", "", 0)) == "invalid JSON (likely HTML/anti-bot page)"
    assert health.reason(httpx.ReadTimeout("read timed out")) == "timeout"
    long = health.reason(RuntimeError("the quick brown fox jumps over the lazy dog " * 5 + "https://a.b/c?id=9"))
    assert len(long) <= 80 and long.endswith("…") and "http" not in long
    assert long[:-1].split()[-1] in "the quick brown fox jumps over the lazy dog".split()  # never mid-word


def test_fingerprint_ignores_urls_and_ids():
    a = health.fingerprint("mediobanca", "intervieweb", _status_error(403, "https://m.it/annunci.php?k=abc&t=1"))
    b = health.fingerprint("mediobanca", "intervieweb", _status_error(403, "https://m.it/other?k=zzz&t=2"))
    assert a == b == "mediobanca:intervieweb:HTTPStatusError:403"
    assert health.fingerprint("x", "workday", json.JSONDecodeError("m", "", 0)) == "x:workday:JSONDecodeError"
    # all timeout flavours are the same problem
    assert health.fingerprint("x", "a", httpx.ReadTimeout("r")) == health.fingerprint("x", "a", httpx.ConnectTimeout("c"))


def test_reminder_after_n_days():
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    ev = health.Event("k", "K (lever)", False, "k:lever:NotFound:404", "404 – board not found")
    prev = health.State("k", "failing", ev.fingerprint, ev.reason, now - timedelta(days=9), now - timedelta(hours=4),
                        50, notified_at=now - timedelta(days=3))
    assert health.decide(prev, ev, now, reminder_days=0).kind is None
    assert health.decide(prev, ev, now, reminder_days=4).kind is None
    assert health.decide(prev, ev, now, reminder_days=3).kind == "reminder"


# ---------------------------------------------------------------- end to end: real runs, mocked HTTP

@pytest.fixture
def settings(tmp_path, monkeypatch):
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS jobradar CASCADE")
    reg = tmp_path / "companies.yaml"
    reg.write_text(REGISTRY)

    async def _nosleep(*_a, **_k):
        return None
    monkeypatch.setattr("asyncio.sleep", _nosleep)
    return Settings(database_url=DSN, telegram_token="T", telegram_chat_id="42", hc_ping_url=None,
                    companies_file=reg, filters_file=ROOT / "config" / "filters.yaml", concurrency=4, notify_max=30,
                    digest_hour=None)


def _run(settings, lever: httpx.Response) -> list[str]:
    """One pipeline run; returns the health messages sent."""
    sent: list[str] = []
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as r:
        r.get("https://boards-api.greenhouse.io/v1/boards/acmegh/jobs").mock(
            return_value=httpx.Response(200, json=json.loads((FX / "greenhouse.json").read_text())))
        r.get(url__regex=r"https://api\.lever\.co/.*").mock(return_value=lever)

        def tg(request):
            sent.append(json.loads(request.content)["text"])
            return httpx.Response(200, json={"ok": True})
        r.post(url__regex=r"https://api\.telegram\.org/.*").mock(side_effect=tg)
        run(settings)
    return [m for m in sent if m.startswith("<b>Job Radar health</b>")]


def _q(sql, *a):
    with psycopg.connect(DSN) as c:
        return c.execute(sql, a).fetchall()


GOOD_LEVER = httpx.Response(200, json=[])


@needs_db
def test_same_failure_three_runs_one_message(settings):
    msgs = [_run(settings, httpx.Response(503)) for _ in range(3)]
    assert [len(m) for m in msgs] == [1, 0, 0]
    assert "Broken Co (lever): 503 – server error" in msgs[0][0]
    assert _q("SELECT status, fingerprint, consecutive_failures FROM jobradar.health_state WHERE company_key='broken'") \
        == [("failing", "broken:lever:HTTPStatusError:503", 3)]


@needs_db
def test_fingerprint_change_sends_new_message(settings):
    assert len(_run(settings, httpx.Response(503))) == 1
    assert _run(settings, httpx.Response(503)) == []
    changed = _run(settings, httpx.Response(404))
    assert len(changed) == 1 and "404 – board not found" in changed[0] and "problem changed" in changed[0]
    assert _run(settings, httpx.Response(404)) == []


@needs_db
def test_recovery_announced_once_then_silence(settings):
    assert len(_run(settings, httpx.Response(503))) == 1
    rec = _run(settings, GOOD_LEVER)
    assert len(rec) == 1 and "✅ Recovered: Broken Co" in rec[0] and "❌" not in rec[0]
    assert _run(settings, GOOD_LEVER) == []
    assert _q("SELECT status, fingerprint, consecutive_failures, notified_at FROM jobradar.health_state "
              "WHERE company_key='broken'") == [("ok", None, 0, None)]


@needs_db
def test_muted_company_never_notifies(settings):
    from jobradar import db
    _run(settings, GOOD_LEVER)                       # creates the company rows
    with psycopg.connect(DSN) as c:
        c.execute("SET search_path TO jobradar, public")
        assert db.set_muted(c, "broken", True)
    assert _run(settings, httpx.Response(503)) == []
    assert _run(settings, httpx.Response(404)) == []  # even a changed problem stays quiet
    assert _run(settings, GOOD_LEVER) == []           # and so does its recovery
    assert _run(settings, httpx.Response(404)) == []
    with psycopg.connect(DSN) as c:                   # unmute while failing → announced once on the next run
        c.execute("SET search_path TO jobradar, public")
        db.set_muted(c, "broken", False)
    again = _run(settings, httpx.Response(404))
    assert len(again) == 1 and "404 – board not found" in again[0]
    assert _run(settings, httpx.Response(404)) == []
