#!/usr/bin/env python3
"""Generate the "Job Radar" Grafana dashboard for Mission Control.

Output goes into Mission Control's file-provisioned dashboards dir (allowUiUpdates=false → edit here, re-run).
Uses the existing `warehouse` datasource (grafana_ro, which has SELECT on schema jobradar).
"""
import json, pathlib, sys

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else
                   "/root/rossettilab/monitoring/grafana/provisioning/dashboards/files/job-radar.json")
WH = {"type": "grafana-postgresql-datasource", "uid": "warehouse"}

panels, _id = [], [0]


def panel(kind, title, sql, x, y, w, h, fmt="table", into=None, **extra):
    _id[0] += 1
    (panels if into is None else into).append({
        "id": _id[0], "type": kind, "title": title, "datasource": WH,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{"refId": "A", "datasource": WH, "rawQuery": True, "editorMode": "code",
                     "format": fmt, "rawSql": sql}], **extra})


def query_var(name, label, sql):
    return {"name": name, "label": label, "type": "query", "datasource": WH, "query": sql, "definition": sql,
            "refresh": 2, "multi": True, "includeAll": True, "current": {"text": "All", "value": "$__all"},
            "sort": 1}


VARIABLES = [
    {"name": "tier", "label": "Tier", "type": "custom", "query": "A,B,C", "multi": True, "includeAll": True,
     "current": {"text": "All", "value": "$__all"},
     "options": [{"text": t, "value": t, "selected": False} for t in "ABC"]},
    query_var("company", "Company", "SELECT name FROM jobradar.companies WHERE enabled ORDER BY 1"),
    query_var("location", "Location",
              "SELECT DISTINCT location_label FROM jobradar.matches WHERE passed AND location_label IS NOT NULL"),
    {**query_var("window", "Window", "SELECT DISTINCT unnest(windows) FROM jobradar.matches WHERE passed"),
     "allValue": "'__all'"},
    {"name": "search", "label": "Title contains", "type": "textbox", "query": "",
     "current": {"text": "", "value": ""}},
    {"name": "min_score", "label": "Min LLM score", "type": "custom", "query": "0,50,65,75,85",
     "current": {"text": "0", "value": "0"},
     "options": [{"text": v, "value": v, "selected": v == "0"} for v in ("0", "50", "65", "75", "85")]},
]
# ${var:sqlstring} quotes/escapes each value, so free-text search can't break out of the SQL string.
TITLE_SEARCH = "(${search:sqlstring} = '' OR p.title ILIKE '%' || ${search:sqlstring} || '%')"
# Window's "All" is the sentinel '__all' (expanding it to every tag would drop untagged postings).
WINDOW_FILTER = "('__all' = ANY(ARRAY[$window]::text[]) OR m.windows && ARRAY[$window]::text[])"

BROWSE_TABLE = {
    "options": {"showHeader": True, "cellHeight": "sm",
                "footer": {"show": True, "reducer": ["count"], "countRows": True, "fields": ""},
                "sortBy": [{"displayName": "tier", "desc": False}]},
    "fieldConfig": {"defaults": {"custom": {"filterable": True, "align": "auto"}},
                    "overrides": [
                        {"matcher": {"id": "byName", "options": "url"}, "properties": [
                            {"id": "custom.width", "value": 90},
                            {"id": "links", "value": [{"title": "Open posting", "url": "${__value.raw}",
                                                       "targetBlank": True}]}]},
                        {"matcher": {"id": "byName", "options": "title"}, "properties": [
                            {"id": "custom.minWidth", "value": 320}]}]},
}


for i, t in enumerate("ABC"):
    panel("stat", f"Open matches · tier {t}",
          f"SELECT count(*) AS \"{t}\" FROM jobradar.v_open_matches WHERE tier = '{t}'", i * 4, 0, 4, 4)
panel("stat", "Open matches · score ≥ 75",
      "SELECT count(*) AS top FROM jobradar.v_open_matches WHERE llm_score >= 75", 12, 0, 4, 4,
      fieldConfig={"defaults": {"color": {"mode": "fixed", "fixedColor": "purple"}}, "overrides": []})
panel("stat", "Last run",
      "SELECT finished_at AS time, status FROM jobradar.runs ORDER BY id DESC LIMIT 1", 16, 0, 4, 4,
      options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^status$/"}, "colorMode": "background"},
      fieldConfig={"defaults": {"mappings": [{"type": "value", "options": {
          "ok": {"color": "green", "index": 0}, "partial": {"color": "orange", "index": 1},
          "failed": {"color": "red", "index": 2}}}]}, "overrides": []})
panel("stat", "Broken boards",
      "SELECT count(*) AS broken FROM jobradar.v_adapter_health "
      "WHERE NOT last_ok OR (last_n = 0 AND prev_max_n > 0)", 20, 0, 4, 4,
      fieldConfig={"defaults": {"thresholds": {"mode": "absolute", "steps": [
          {"color": "green", "value": None}, {"color": "red", "value": 1}]}}, "overrides": []},
      options={"colorMode": "background"})
panel("barchart", "New matches per day",
      "SELECT date_trunc('day', p.first_seen) AS time, count(*) AS matches FROM jobradar.matches m "
      "JOIN jobradar.postings p USING (uid) WHERE m.passed AND $__timeFilter(p.first_seen) GROUP BY 1 ORDER BY 1",
      0, 4, 12, 8, fmt="time_series", options={"xTickLabelRotation": -45})
panel("timeseries", "Postings seen per run",
      "SELECT finished_at AS time, postings_seen, new_postings, companies_failed FROM jobradar.runs "
      "WHERE $__timeFilter(finished_at) ORDER BY 1", 12, 4, 12, 8, fmt="time_series")
panel("table", "Broken adapters",
      "SELECT company_key, last_run, last_ok, last_n, prev_max_n, last_error FROM jobradar.v_adapter_health "
      "WHERE NOT last_ok OR (last_n = 0 AND prev_max_n > 0) ORDER BY company_key", 0, 12, 24, 6)
MATCH_TABLE = {**BROWSE_TABLE, "options": {**BROWSE_TABLE["options"], "sortBy": [{"displayName": "score", "desc": True}]}}
MATCH_TABLE["fieldConfig"] = {**BROWSE_TABLE["fieldConfig"], "overrides": BROWSE_TABLE["fieldConfig"]["overrides"] + [
    {"matcher": {"id": "byName", "options": "score"}, "properties": [
        {"id": "custom.width", "value": 70}, {"id": "custom.cellOptions", "value": {"type": "color-background"}},
        {"id": "thresholds", "value": {"mode": "absolute", "steps": [
            {"color": "transparent", "value": None}, {"color": "orange", "value": 65}, {"color": "green", "value": 75}]}}]},
    {"matcher": {"id": "byName", "options": "reason"}, "properties": [{"id": "custom.minWidth", "value": 300}]}]}
panel("table", "Open matches — all (sorted by LLM score; click a header to sort, funnel icon to filter)",
      "SELECT m.llm_score AS score, (m.llm_json->>'score')::int AS raw, m.llm_json->>'route' AS route, c.tier, c.name AS company, p.title, "
      "m.location_label AS location, array_to_string(m.windows, ', ') AS windows, m.llm_json->>'reason' AS reason, "
      "m.level, c.bucket, p.posted_at, p.first_seen, p.url "
      "FROM jobradar.matches m JOIN jobradar.postings p USING (uid) JOIN jobradar.companies c ON c.key = p.company_key "
      "WHERE m.passed AND p.closed_at IS NULL AND c.tier IN ($tier) AND c.name IN ($company) "
      f"AND m.location_label IN ($location) AND {WINDOW_FILTER} AND {TITLE_SEARCH} "
      "AND COALESCE(m.llm_score, 0) >= $min_score "
      "ORDER BY m.llm_score DESC NULLS LAST, c.tier, c.name, p.title", 0, 18, 24, 20, **MATCH_TABLE)

# Collapsed rows only query their panels when expanded, so the ~10k-row table costs nothing until opened.
all_postings = []
panel("table", "All open postings — including ones the filters rejected (reason shown)",
      "SELECT c.tier, c.name AS company, p.title, coalesce(m.location_label, p.location) AS location, "
      "CASE WHEN m.passed THEN 'match' ELSE coalesce(m.reasons[1], 'not evaluated') END AS filter, "
      "p.employment_type, p.department, p.posted_at, p.first_seen, p.url "
      "FROM jobradar.postings p JOIN jobradar.companies c ON c.key = p.company_key "
      "LEFT JOIN jobradar.matches m USING (uid) "
      f"WHERE p.closed_at IS NULL AND c.tier IN ($tier) AND c.name IN ($company) AND {TITLE_SEARCH} "
      "ORDER BY c.tier, c.name, p.title", 0, 39, 24, 22, into=all_postings, **BROWSE_TABLE)
_id[0] += 1
panels.append({"id": _id[0], "type": "row", "title": "All open postings (expand to load)", "collapsed": True,
               "gridPos": {"x": 0, "y": 38, "w": 24, "h": 1}, "panels": all_postings})

dash = {"uid": "job-radar", "title": "Job Radar", "tags": ["jobradar"], "timezone": "browser",
        "schemaVersion": 39, "editable": False, "time": {"from": "now-30d", "to": "now"},
        "refresh": "15m", "templating": {"list": VARIABLES}, "panels": panels}
OUT.write_text(json.dumps(dash, indent=1))
print("wrote", OUT)
