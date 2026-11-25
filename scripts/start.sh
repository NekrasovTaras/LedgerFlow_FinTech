#!/usr/bin/env sh
set -eu
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_root"
if [ "${1:-}" = "--monitoring" ]; then
  docker compose --project-name ledgerflow --file "$project_root/compose.yaml" --profile monitoring up --build --detach --wait --wait-timeout 180
else
  docker compose --project-name ledgerflow --file "$project_root/compose.yaml" up --build --detach --wait --wait-timeout 180
fi
printf '%s\n' 'LedgerFlow UI: http://localhost:5173' 'LedgerFlow API: http://localhost:8000/docs'
