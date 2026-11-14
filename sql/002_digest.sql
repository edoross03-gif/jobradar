-- Daily digest bookkeeping (idempotent): the run that sent the tier B/C digest gets a timestamp.
SET search_path TO jobradar, public;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS digest_sent_at timestamptz;
