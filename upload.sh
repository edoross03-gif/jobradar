#!/usr/bin/env bash
# Run on YOUR PC, from inside the jobradar folder.
#   ./upload.sh           send the folder to the server + show what would change live (changes nothing live)
#   ./upload.sh apply     send + deploy (tests run first; asks for confirmation)
set -euo pipefail
cd "$(dirname "$0")"
[ -f .deploy.env ] && . ./.deploy.env   # holds SERVER=user@host; not in git, see .deploy.env.example
: "${SERVER:?set SERVER in .deploy.env (copy .deploy.env.example)}"
rsync -rlc --delete --exclude='.git/' --exclude='.DS_Store' --exclude='*.bak*' --exclude='.env' \
  --exclude='__pycache__/' --exclude='.pytest_cache/' --exclude='.venv/' \
  --exclude='/export.sh' --exclude='/exports/' \
  --exclude='/docs/' --exclude='/_*/' --exclude='/.claude/' --exclude='/.gitattributes' --exclude='/.deploy.env*' --exclude='desktop.ini' \
  ./ "$SERVER:/opt/jobradar/"
ssh -t "$SERVER" "/opt/jobradar/deploy.sh ${1:-status}"
