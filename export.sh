#!/usr/bin/env bash
# Run on YOUR PC, from inside the jobradar folder (Git Bash or WSL). Read-only: changes nothing on the server.
# Dumps the live Job Radar database to a CSV in ./exports/ (one row per posting, Excel-friendly UTF-8).
#   ./export.sh            open postings that passed the filter   -> exports/jobradar_open_matches.csv
#   ./export.sh matches    every posting that passed (incl. closed) -> exports/jobradar_matches.csv
#   ./export.sh all        every posting ever seen                  -> exports/jobradar_all_postings.csv
set -euo pipefail
cd "$(dirname "$0")"
[ -f .deploy.env ] && . ./.deploy.env   # holds SERVER=user@host; not in git, see .deploy.env.example
: "${SERVER:?set SERVER in .deploy.env (copy .deploy.env.example)}"
scope=${1:-open}
case $scope in
  open)    out=exports/jobradar_open_matches.csv ;;
  matches) out=exports/jobradar_matches.csv ;;
  all)     out=exports/jobradar_all_postings.csv ;;
  *) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
mkdir -p exports
ssh "$SERVER" "docker exec -i -e EXPORT_SCOPE=$scope jobradar python -" > "$out.tmp" <<'PY'
import csv, datetime, io, os, sys
import psycopg

WHERE = {"open": "m.passed AND p.closed_at IS NULL", "matches": "m.passed", "all": "TRUE"}[os.environ["EXPORT_SCOPE"]]
SQL = f"""
SELECT c.tier, c.name AS company, c.bucket, p.title, p.url,
       p.location, m.location_label, p.city, p.country, p.remote, p.department, p.employment_type,
       p.posted_at, p.first_seen, p.last_seen, p.closed_at, (p.closed_at IS NULL) AS is_open,
       m.passed AS passed_filter, m.level AS filter_level,
       array_to_string(m.windows, '; ') AS windows, array_to_string(m.reasons, '; ') AS filter_reasons,
       m.llm_score AS score_weighted, m.llm_json->>'score' AS score_raw,
       m.llm_json->>'route' AS llm_route, m.llm_json->>'window' AS llm_window,
       m.llm_json->>'skill_fit' AS skill_fit, m.llm_json->>'eligibility' AS eligibility,
       m.llm_json->>'location_fit' AS location_fit, m.llm_json->>'track_alignment' AS track_alignment,
       m.llm_json->>'reason' AS llm_reason, m.scored_at, m.notified_at,
       a.status AS application_status, a.applied_at,
       p.source, c.careers_url, p.uid, p.description
FROM jobradar.postings p
JOIN jobradar.companies c ON c.key = p.company_key
LEFT JOIN jobradar.matches m USING (uid)
LEFT JOIN jobradar.applications a USING (uid)
WHERE {WHERE}
ORDER BY (p.closed_at IS NULL) DESC, m.passed DESC NULLS LAST, m.llm_score DESC NULLS LAST, c.tier, p.first_seen DESC
"""

def cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, datetime.datetime):
        return v.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
    return v

out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8-sig", newline="")
w = csv.writer(out)
n = 0
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    conn.execute("SET default_transaction_read_only = on")
    with conn.cursor(name="export") as cur:
        cur.execute(SQL)
        w.writerow([d.name for d in cur.description])
        for row in cur:
            w.writerow([cell(v) for v in row])
            n += 1
out.flush()
print(f"{n} rows", file=sys.stderr)
PY
mv "$out.tmp" "$out"
echo "wrote $out ($(du -h "$out" | cut -f1))"
