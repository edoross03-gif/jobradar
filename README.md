# Job Radar

The system I use to find my next role. It watches the career boards of the companies I care about, keeps only the
early-career roles that fit, scores each one against my profile with an LLM and tells me on Telegram before the
posting is a day old.

```
config/companies.yaml ─► adapters (public job boards) ─► Postgres ─► rules filter ─► LLM score ─► Telegram
                                                            │
                                              board health + healthchecks.io
```

Python 3.12, Postgres 16, Docker. No scraping behind logins and no LinkedIn requests: only the public endpoints the
boards themselves expose.

## What it does

- **Fetches** every company in the registry on an interval (2 hours on my server) through 24 job-board adapters.
- **Stores** every posting in Postgres. Postings that disappear are closed, and reopened if they come back.
- **Filters** with explainable rules (`config/filters.yaml`): seniority, function, location, and a tag for the
  recruiting window (`summer_2027`, `off_cycle`, `grad_2028`, ...). Cheap and tuned for recall.
- **Scores** what passes against `config/profile.yaml` with Gemini, constrained to a JSON schema: an overall score,
  four sub-scores (skill fit, eligibility, track alignment, location fit), a route (`apply_cold`,
  `referral_needed`, `skip`) and a one-line reason. Posting text is passed as data, never as instructions.
- **Alerts** on Telegram. Top-tier companies and high scores alert on every run; the rest go into one daily digest.
- **Watches itself.** A board that breaks alerts once, then stays quiet until something changes. A board that had
  postings and suddenly returns none is treated as broken, and nothing is closed.

## Coverage

One adapter per platform, each with a parser test on a saved fixture:

`amazon` · `ashby` · `avature` · `bendingspoons` · `cornerstone` · `eightfold` · `empregoestagios` · `goldman` ·
`greenhouse` · `htmllinks` · `intervieweb` · `jibe` · `lever` · `oracle_hcm` · `personio` · `phenom` · `pinpoint` ·
`recruitee` · `smartrecruiters` · `successfactors` · `talentsoft` · `teamtailor` · `workable` · `workday`

`discover` works out which platform a company uses from its careers page, so adding a company is usually one line.

## Set up

```bash
cp .env.example .env
```

```bash
cp config/profile.example.yaml config/profile.yaml
```

```bash
cp config/companies.example.yaml config/companies.yaml
```

Fill `.env` (database URL, Telegram bot, Gemini key, healthchecks.io ping), write your own profile, list your own
companies. The example registry has one company per adapter. Then either join an existing Postgres:

```bash
cp docker-compose.override.example.yml docker-compose.override.yml && docker compose up -d
```

or let it bring its own:

```bash
docker compose --profile standalone up -d
```

## Commands

```bash
python -m jobradar discover            # work out the platform of companies without `ats`; prints a report
python -m jobradar discover --write    # ...and write it to config/companies.yaml. Review the report first
python -m jobradar dry-run --only stripe openai [--rejects]   # fetch + filter, no database: tune filters.yaml
python -m jobradar run                 # one full run
python -m jobradar loop --every 2h     # service mode (the Docker default)
python -m jobradar report --tier A [--send]
python -m jobradar refilter [--notify] # after editing filters.yaml
python -m jobradar score [--rescore]   # LLM-score open matches; --rescore after editing profile.yaml
python -m jobradar health              # boards failing right now
python -m jobradar mute KEY            # silence health alerts for one company (unmute KEY to undo)
```

## Behaviour worth knowing

- **Baseline.** The first successful fetch of a company stores its matches silently and sends one summary message.
  After that, only new postings alert.
- **Failures never kill a run.** They are recorded per company and summarised in one health message.
- **Tiers.** Each company has a tier (A, B, C). `INSTANT_TIERS` alert on every run. The score shown is the LLM
  score times the tier weight, and anything at `INSTANT_SCORE` or above alerts at once whatever the tier.
- **Scoring failures** leave a match unscored. It is retried on the next run.

## Data

Everything lives in the Postgres schema `jobradar`: `companies`, `runs`, `company_runs`, `postings`, `matches`,
`applications`, `health_state`, and the views `v_open_matches` and `v_adapter_health`. Migrations are in `sql/`.
`./export.sh` dumps the open matches (or everything) to a CSV. `grafana/build_dashboard.py` builds the dashboard I
keep next to my server monitoring.

## Tests

```bash
pip install -r requirements-dev.txt
```

```bash
TEST_DATABASE_URL=postgresql://user:pass@host:5432/scratch_db pytest
```

The end-to-end tests **drop the schema `jobradar`** in that database: point it at a scratch one. Without
`TEST_DATABASE_URL` they are skipped and the parser, filter, discovery and health-logic tests still run.

## Deploying it

I edit on my PC and deploy with `./upload.sh`, which copies the folder to the server, runs the tests there, shows the
difference with what is live and asks before applying. The workflow is in [DESKTOP.md](DESKTOP.md). The server address
comes from `.deploy.env` (see `.deploy.env.example`).

## Status

Running since late September 2026. Next: an application tracker on top of `applications`, and adapters for the boards
that sit behind a browser challenge.
