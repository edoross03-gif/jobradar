-- Per-company health bookkeeping (idempotent): alert once per problem, then stay quiet until it changes.
SET search_path TO jobradar, public;
CREATE TABLE IF NOT EXISTS health_state (
    company_key           text PRIMARY KEY REFERENCES companies(key) ON DELETE CASCADE,
    status                text NOT NULL DEFAULT 'ok' CHECK (status IN ('failing', 'ok')),
    fingerprint           text,             -- company:adapter:error class[:http status], or anomaly:empty_board
    reason                text,             -- short human reason shown in alerts / `jobradar health`
    first_failed_at       timestamptz,
    last_failed_at        timestamptz,
    consecutive_failures  int NOT NULL DEFAULT 0,
    notified_at           timestamptz,      -- NULL while failing = not yet announced (or Telegram failed)
    muted                 boolean NOT NULL DEFAULT false
);
