#!/usr/bin/env bash
# Job Radar deploy (runs ON THE SERVER): copies /opt/jobradar to the live folder /opt/rossettilab/jobradar.
#
#   deploy.sh status            what differs from live + anything changed ON the server since last deploy
#   deploy.sh apply             tests → show changes → confirm → backup → copy → rebuild only if code changed
#   deploy.sh apply --yes       same, no confirmation prompt
#   deploy.sh rollback          restore the latest backup (and rebuild)
#
# Never touched: .env, *.bak*, config/discover_report_*.md, caches. No Caddy, n8n, DB or schema changes.
set -euo pipefail

SRC=/opt/jobradar
LIVE=/opt/rossettilab/jobradar
BACKUPS=/opt/jobradar-backups
MANIFEST=$BACKUPS/live.md5          # checksums of live right after the last deploy
IMAGE=jobradar-jobradar
TS=$(date +%Y%m%d-%H%M%S)

die(){ echo "✗ $*" >&2; exit 1; }
ok(){ echo "✓ $*"; }
usage(){ sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

# Excluded = never copied AND never deleted on the live side.
F=(--exclude='.env' --exclude='*.bak*' --exclude='discover_report_*.md' --exclude='__pycache__/'
   --exclude='.pytest_cache/' --exclude='.git/' --exclude='.DS_Store' --exclude='.claude/'
   --exclude='/deploy.sh' --exclude='/upload.sh' --exclude='/download.sh' --exclude='/DESKTOP.md')

changes(){ rsync -rlc --delete --dry-run --itemize-changes "${F[@]}" "$SRC/" "$LIVE/" \
             | grep -E '^(\*deleting|[<>c]f|cd\+)' || true; }

live_sums(){ (cd "$LIVE" && find . -type f ! -name '.env' ! -name '*.bak*' ! -name 'discover_report_*.md' \
               ! -path '*/__pycache__/*' ! -path './.pytest_cache/*' -print0 | sort -z | xargs -0 md5sum); }

# Files modified on the server (e.g. `discover --write` edits config/companies.yaml) since the last deploy.
server_drift(){
  [ -f "$MANIFEST" ] || { echo "(no manifest yet: first deploy)"; return 0; }
  diff <(cat "$MANIFEST") <(live_sums) | grep '^>' | awk '{print "  " $3}' || true
}

run_tests(){
  echo "→ running tests on /opt/jobradar (current image, no DB)"
  docker run --rm --network none -v "$SRC":/app -w /app --entrypoint python "$IMAGE" -m pytest -q \
    || die "tests failed: nothing deployed"
  docker run --rm --network none -v "$SRC":/app -w /app --entrypoint python "$IMAGE" -c \
    "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('config/*.yaml')]" \
    || die "a config/*.yaml file does not parse: nothing deployed"
  ok "tests + YAML ok"
}

cmd=${1:-status}; yes=${2:-}
[ -d "$SRC" ] || die "$SRC missing"
mkdir -p "$BACKUPS"; chmod 700 "$BACKUPS"

case $cmd in
  status)
    echo "== Would change on live (desktop → server):"; C=$(changes); echo "${C:-  nothing}"
    echo "== Changed on the server since last deploy (would be OVERWRITTEN, download first):"
    D=$(server_drift); echo "${D:-  nothing}" ;;

  apply)
    C=$(changes); [ -n "$C" ] || { ok "live already matches, nothing to do"; exit 0; }
    D=$(server_drift)
    if [ -n "$D" ] && [ "$D" != "(no manifest yet: first deploy)" ]; then
      echo "These live files changed on the server since your last deploy:"; echo "$D"
      die "run ./download.sh on your PC first (or merge by hand), then upload again"
    fi
    run_tests
    echo "== Changes:"; echo "$C"
    if [ "$yes" != "--yes" ]; then read -rp "Apply to live? [y/N] " a; [ "$a" = y ] || die "aborted"; fi
    tar -C "$(dirname "$LIVE")" -czf "$BACKUPS/jobradar-$TS.tar.gz" "$(basename "$LIVE")"
    ok "backup $BACKUPS/jobradar-$TS.tar.gz"
    rsync -rlc --delete "${F[@]}" "$SRC/" "$LIVE/"
    live_sums > "$MANIFEST"
    if echo "$C" | awk '{print $2}' | grep -qvE '^config/'; then
      echo "→ code changed: rebuilding container"
      (cd "$LIVE" && docker compose up -d --build jobradar)
      sleep 5; docker ps --filter name='^jobradar$' --format '{{.Status}}' | grep -q '^Up' \
        || die "container not running: check 'docker logs jobradar' or run: deploy.sh rollback"
      ok "jobradar rebuilt and running"
    else
      ok "config only: picked up on the next run, no restart"
    fi ;;

  rollback)
    B=$(ls -1t "$BACKUPS"/jobradar-*.tar.gz 2>/dev/null | head -1); [ -n "$B" ] || die "no backup"
    [ "$yes" = "--yes" ] || { read -rp "Restore $B? [y/N] " a; [ "$a" = y ] || die "aborted"; }
    T=$(mktemp -d); tar -C "$T" -xzf "$B"
    rsync -rlc --delete --exclude='.env' "$T/jobradar/" "$LIVE/"; rm -rf "$T"
    live_sums > "$MANIFEST"
    (cd "$LIVE" && docker compose up -d --build jobradar); ok "restored $B" ;;

  *) usage ;;
esac
