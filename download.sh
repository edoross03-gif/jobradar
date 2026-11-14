#!/usr/bin/env bash
# Run on YOUR PC, from inside the jobradar folder.
# Pulls the LIVE Job Radar files into this folder (e.g. after `discover --write` changed companies.yaml on the server).
# Overwrites your local copies of those files; commit first if you use git. Never pulls .env or backups.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .deploy.env ] && . ./.deploy.env   # holds SERVER=user@host; not in git, see .deploy.env.example
: "${SERVER:?set SERVER in .deploy.env (copy .deploy.env.example)}"
rsync -rlcv --exclude='.env' --exclude='*.bak*' --exclude='discover_report_*.md' \
  --exclude='__pycache__/' --exclude='.pytest_cache/' \
  "$SERVER:/opt/rossettilab/jobradar/" ./
