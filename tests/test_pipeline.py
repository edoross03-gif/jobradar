"""End-to-end: mocked HTTP → real Postgres. Needs TEST_DATABASE_URL (skipped otherwise)."""
import copy
import json
import os
from pathlib import Path

import httpx
import psycopg
import pytest
import respx

from jobradar.config import ROOT, Settings
from jobradar.runner import run

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
FX = Path(__file__).parent / "fixtures"

REGISTRY = """companies:
- {name: Acme GH, key: acme-gh, tier: A, bucket: fintech, ats: greenhouse, slug: acmegh}
- {name: Acme Ashby, key: acme-ashby, tier: B, bucket: ai, ats: ashby, slug: acmeashby}
- {name: Acme WD, key: acme-wd, tier: C, bucket: corp, ats: workday, config: {host: acme.wd3.myworkdayjobs.com, tenant: acme, site: Ext}}
- {name: Broken Co, key: broken, tier: C, bucket: corp, ats: lever, slug: broken}
- {name: Unresolved Co, key: unresolved, tier: C, bucket: corp}
"""


@pytest.fixture
def settings(tmp_path):
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS jobradar CASCADE")
    reg = tmp_path / "companies.yaml"
    reg.write_text(REGISTRY)
    return Settings(database_url=DSN, telegram_token="T", telegram_chat_id="42", hc_ping_url="https://hc-ping.com/abc",
                    companies_file=reg, filters_file=ROOT / "config" / "filters.yaml", concurrency=4, notify_max=30,
                    digest_hour=None)  # digest off by default so results don't depend on the wall clock


def mock_all(respx, gh_payload, sent, ashby_payload=None):
    respx.get("https://boards-api.greenhouse.io/v1/boards/acmegh/jobs").mock(return_value=httpx.Response(200, json=gh_payload))
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acmeashby").mock(
        return_value=httpx.Response(200, json=ashby_payload or json.loads((FX / "ashby.json").read_text())))
    respx.post(url__regex=r"https://acme\.wd3\.myworkdayjobs\.com/wday/cxs/acme/Ext/jobs").mock(
        return_value=httpx.Response(200, json=json.loads((FX / "workday.json").read_text())))
    respx.get(url__regex=r"https://api\.lever\.co/.*").mock(return_value=httpx.Response(503))

    def tg(request):
        sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True})
    respx.post(url__regex=r"https://api\.telegram\.org/.*").mock(side_effect=tg)
    respx.post(url__regex=r"https://hc-ping\.com/.*").mock(return_value=httpx.Response(200))


def q(sql, *a):
    with psycopg.connect(DSN) as c:
        return c.execute(sql, a).fetchall()


def test_full_lifecycle(settings, monkeypatch):
    with respx.mock(assert_all_mocked=True) as router:
        _lifecycle(router, settings, monkeypatch)


def _lifecycle(router, settings, monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _nosleep)
    gh = json.loads((FX / "greenhouse.json").read_text())
    sent: list[str] = []

    # ---- Run 1: baseline → no per-posting spam, one baseline summary, broken co reported ----
    mock_all(router, gh, sent)
    res = run(settings)
    assert res["status"] == "partial" and res["companies_ok"] == 3 and res["companies_failed"] == 1
    assert any("baseline" in m for m in sent) and any("Broken Co" in m for m in sent)
    assert not any(m.startswith("<b>Job Radar</b> —") for m in sent)
    n_pass = q("SELECT count(*) FROM jobradar.matches WHERE passed")[0][0]
    # GH: Finance Intern (Lisbon) | Ashby: Working Student + PM Intern | WD: Finance Intern Zurich
    assert n_pass == 4
    assert q("SELECT count(*) FROM jobradar.matches WHERE notified_at IS NULL")[0][0] == 0

    # ---- Run 2: one new GH posting, one removed → notify new, close removed ----
    sent.clear(); router.reset(); router.routes.clear()
    gh2 = copy.deepcopy(gh)
    gh2["jobs"] = [j for j in gh2["jobs"] if j["id"] != 4003]
    gh2["jobs"].append({"id": 4999, "title": "Treasury Intern", "absolute_url": "https://x/4999",
                        "location": {"name": "Milan, Italy"}, "updated_at": "2026-09-28T00:00:00Z", "content": ""})
    mock_all(router, gh2, sent)
    res = run(settings)
    assert res["new_postings"] == 1 and res["new_matches"] == 1
    alerts = [m for m in sent if m.startswith("<b>Job Radar</b> —")]
    assert len(alerts) == 1 and "Treasury Intern" in alerts[0] and "🔥" in alerts[0]
    closed = q("SELECT external_id FROM jobradar.postings WHERE closed_at IS NOT NULL")
    assert closed == [("4003",)]

    # ---- Run 3: GH suddenly empty → anomaly, nothing closed ----
    sent.clear(); router.reset(); router.routes.clear()
    mock_all(router, {"jobs": []}, sent)
    res = run(settings)
    assert any("0 postings (was 5)" in a for a in res["anomalies"])
    assert q("SELECT count(*) FROM jobradar.postings WHERE company_key='acme-gh' AND closed_at IS NULL")[0][0] == 5

    # views work
    assert q("SELECT count(*) FROM jobradar.v_open_matches")[0][0] == 5
    assert q("SELECT count(*) FROM jobradar.runs")[0][0] == 3

    # ---- Run 4: new tier-B match → no instant alert, held for the digest ----
    ashby = json.loads((FX / "ashby.json").read_text())
    new_b = {**ashby["jobs"][0], "id": "uuid-9", "title": "Finance Intern", "jobUrl": "https://jobs.ashbyhq.com/acme/uuid-9"}
    ashby["jobs"].append(new_b)
    sent.clear(); router.reset(); router.routes.clear()
    mock_all(router, gh2, sent, ashby)
    res = run(settings)
    assert res["new_matches"] == 1
    assert not any(m.startswith("<b>Job Radar</b> —") or "digest" in m for m in sent)
    assert q("SELECT count(*) FROM jobradar.matches WHERE passed AND notified_at IS NULL")[0][0] == 1

    # ---- Run 5: digest due (hour 0 = any time today) → one digest with the tier-B match ----
    settings.digest_hour = 0
    sent.clear(); router.reset(); router.routes.clear()
    mock_all(router, gh2, sent, ashby)
    run(settings)
    digests = [m for m in sent if m.startswith("<b>Job Radar daily digest</b>")]
    assert len(digests) == 1 and "Finance Intern" in digests[0] and "tier B" in digests[0]
    assert q("SELECT count(*) FROM jobradar.matches WHERE passed AND notified_at IS NULL")[0][0] == 0

    # ---- Run 6: same day → no second digest ----
    sent.clear(); router.reset(); router.routes.clear()
    mock_all(router, gh2, sent, ashby)
    run(settings)
    assert not any("digest" in m for m in sent)

    # ---- Run 7: LLM scoring on → a new tier-B match scoring 88 becomes instant, with the score in the alert ----
    ashby["jobs"].append({**ashby["jobs"][0], "id": "uuid-10", "title": "Strategy Intern",
                          "jobUrl": "https://jobs.ashbyhq.com/acme/uuid-10"})
    settings.gemini_api_key, settings.digest_hour = "test-key", None
    sent.clear(); router.reset(); router.routes.clear()
    mock_all(router, gh2, sent, ashby)
    verdict = {"score": 88, "skill_fit": 90, "eligibility": 85, "track_alignment": 90, "location_fit": 80,
               "route": "apply_cold", "window": "off_cycle", "reason": "Strategy internship in Berlin; strong fit."}
    router.post(url__regex=r"https://generativelanguage\.googleapis\.com/.*").mock(return_value=httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(verdict)}]}}],
                   "usageMetadata": {"totalTokenCount": 1234}}))
    res = run(settings)
    alerts = [m for m in sent if m.startswith("<b>Job Radar</b> —")]
    assert res["new_matches"] == 1 and len(alerts) == 1
    assert "Strategy Intern" in alerts[0] and "⭐ 88" in alerts[0] and "apply" in alerts[0]
    assert q("SELECT llm_score, llm_json->>'route' FROM jobradar.matches m JOIN jobradar.postings p USING (uid) "
             "WHERE p.title = 'Strategy Intern'") == [(88, "apply_cold")]

    # tier weights re-weight stored scores in SQL (no LLM call): tier B at 0.5 -> 44, back to 1.0 -> 88
    from jobradar import db
    with psycopg.connect(DSN) as c:
        c.execute("SET search_path TO jobradar, public")
        db.apply_tier_weights(c, {"B": 0.5})
        assert c.execute("SELECT m.llm_score FROM matches m JOIN postings p USING (uid) "
                         "WHERE p.title = 'Strategy Intern'").fetchone() == (44,)
        db.apply_tier_weights(c, {"A": 1.5, "B": 1.0, "C": 0.66})
        assert c.execute("SELECT m.llm_score FROM matches m JOIN postings p USING (uid) "
                         "WHERE p.title = 'Strategy Intern'").fetchone() == (88,)


async def _nosleep(*_a, **_k):
    return None
