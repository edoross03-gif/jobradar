-- Job Radar schema (idempotent)
CREATE SCHEMA IF NOT EXISTS jobradar;
SET search_path TO jobradar, public;

CREATE TABLE IF NOT EXISTS companies (
    key          text PRIMARY KEY,
    name         text NOT NULL,
    tier         text NOT NULL DEFAULT 'B',
    bucket       text,
    ats          text,
    slug         text,
    config       jsonb NOT NULL DEFAULT '{}'::jsonb,
    careers_url  text,
    enabled      boolean NOT NULL DEFAULT true,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
    id                bigserial PRIMARY KEY,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    status            text NOT NULL DEFAULT 'running',     -- running | ok | partial | failed
    companies_ok      int NOT NULL DEFAULT 0,
    companies_failed  int NOT NULL DEFAULT 0,
    postings_seen     int NOT NULL DEFAULT 0,
    new_postings      int NOT NULL DEFAULT 0,
    new_matches       int NOT NULL DEFAULT 0,
    notes             text
);

CREATE TABLE IF NOT EXISTS company_runs (
    run_id       bigint REFERENCES runs(id) ON DELETE CASCADE,
    company_key  text REFERENCES companies(key) ON DELETE CASCADE,
    ok           boolean NOT NULL,
    n_postings   int,
    error        text,
    duration_ms  int,
    PRIMARY KEY (run_id, company_key)
);

CREATE TABLE IF NOT EXISTS postings (
    uid              text PRIMARY KEY,
    company_key      text NOT NULL REFERENCES companies(key) ON DELETE CASCADE,
    source           text NOT NULL,
    external_id      text NOT NULL,
    title            text NOT NULL,
    url              text,
    location         text,
    city             text,
    country          text,
    remote           boolean,
    department       text,
    employment_type  text,
    posted_at        timestamptz,
    description      text,
    raw              jsonb,
    first_seen       timestamptz NOT NULL DEFAULT now(),
    last_seen        timestamptz NOT NULL DEFAULT now(),
    closed_at        timestamptz
);
CREATE INDEX IF NOT EXISTS postings_company_open ON postings(company_key) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS postings_first_seen ON postings(first_seen DESC);

CREATE TABLE IF NOT EXISTS matches (
    uid             text PRIMARY KEY REFERENCES postings(uid) ON DELETE CASCADE,
    passed          boolean NOT NULL,
    level           text,                 -- strong | weak
    location_label  text,
    windows         text[] NOT NULL DEFAULT '{}',
    reasons         text[] NOT NULL DEFAULT '{}',
    evaluated_at    timestamptz NOT NULL DEFAULT now(),
    -- Stage 2 (Sprint 2): LLM scoring
    llm_score       int,
    llm_json        jsonb,
    scored_at       timestamptz,
    notified_at     timestamptz
);
CREATE INDEX IF NOT EXISTS matches_pending ON matches(passed, notified_at);

-- Sprint 3/4 will use this; created now so the pipeline has a place to land.
CREATE TABLE IF NOT EXISTS applications (
    uid          text PRIMARY KEY REFERENCES postings(uid) ON DELETE CASCADE,
    status       text NOT NULL DEFAULT 'saved',   -- saved | applied | interview | offer | rejected | ghosted
    applied_at   timestamptz,
    cv_variant   text,
    referral     text,
    notes        text,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE VIEW v_open_matches AS
SELECT c.tier, c.name AS company, p.title, m.location_label, m.windows, m.level, m.llm_score,
       p.url, p.posted_at, p.first_seen, p.uid
FROM matches m
JOIN postings p USING (uid)
JOIN companies c ON c.key = p.company_key
WHERE m.passed AND p.closed_at IS NULL
ORDER BY c.tier, p.first_seen DESC;

-- Adapter health: companies whose last successful fetch returned 0 but used to return > 0.
CREATE OR REPLACE VIEW v_adapter_health AS
WITH ranked AS (
    SELECT cr.*, r.started_at,
           row_number() OVER (PARTITION BY cr.company_key ORDER BY cr.run_id DESC) AS rn
    FROM company_runs cr JOIN runs r ON r.id = cr.run_id
)
SELECT company_key,
       max(CASE WHEN rn = 1 THEN started_at END)  AS last_run,
       bool_or(rn = 1 AND ok)                     AS last_ok,
       max(CASE WHEN rn = 1 THEN n_postings END)  AS last_n,
       max(CASE WHEN rn BETWEEN 2 AND 6 AND ok THEN n_postings END) AS prev_max_n,
       max(CASE WHEN rn = 1 THEN error END)       AS last_error
FROM ranked WHERE rn <= 6
GROUP BY company_key;
