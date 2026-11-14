import asyncio
import json
from pathlib import Path

import httpx
import respx

from jobradar.discover import apply, discover, report
from jobradar.models import Company

FX = Path(__file__).parent / "fixtures"


def _run(companies):
    return asyncio.run(discover(companies, concurrency=2, max_candidates=3))


def test_fingerprint_then_slug_probe():
    ashby_fx = json.loads((FX / "ashby.json").read_text())
    with respx.mock(assert_all_mocked=False) as r:
        # Company 1: careers page embeds Ashby
        r.get("https://acme.example/careers").mock(return_value=httpx.Response(
            200, text='<a href="https://jobs.ashbyhq.com/acme-labs/uuid">Open roles</a>'))
        r.get("https://api.ashbyhq.com/posting-api/job-board/acme-labs").mock(return_value=httpx.Response(200, json=ashby_fx))
        # Company 2: no careers url; slug "betaco" lives on Greenhouse
        r.get("https://boards-api.greenhouse.io/v1/boards/betaco/jobs").mock(return_value=httpx.Response(
            200, json=json.loads((FX / "greenhouse.json").read_text())))
        # Company 3: careers page on SuccessFactors (unsupported yet)
        r.get("https://gamma.example/jobs").mock(return_value=httpx.Response(
            200, text='<iframe src="https://career5.successfactors.eu/career?company=gamma"></iframe>'))
        r.route().mock(return_value=httpx.Response(404))   # everything else: not found
        cs = [Company(key="acme", name="Acme Labs", tier="A", careers_url="https://acme.example/careers"),
              Company(key="beta", name="BetaCo", tier="B"),
              Company(key="gamma", name="Gamma", tier="C", careers_url="https://gamma.example/jobs")]
        res = {x.company.key: x for x in _run(cs)}

    assert res["acme"].hit.ats == "ashby" and res["acme"].hit.slug == "acme-labs" and res["acme"].method == "careers_page"
    assert res["beta"].hit.ats == "greenhouse" and res["beta"].method == "slug_probe" and res["beta"].sample
    assert res["gamma"].hit is None and "successfactors" in res["gamma"].detected_unsupported
    apply(list(res.values()))
    assert cs[0].ats == "ashby" and cs[1].slug == "betaco" and "successfactors" in cs[2].notes
    assert "Resolved 2/3" in report(list(res.values()))
