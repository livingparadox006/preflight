#!/usr/bin/env bash
# Start Preflight Companion detached from the terminal.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
mkdir -p data

if [[ -f data/app.pid ]] && kill -0 "$(cat data/app.pid)" 2>/dev/null; then
  echo "Preflight is already running (pid $(cat data/app.pid))."
  exit 0
fi

setsid ./run.sh "$@" > data/app.log 2>&1 < /dev/null &
echo $! > data/app.pid
echo "Preflight Companion started (pid $(cat data/app.pid))."
echo "Open http://127.0.0.1:8090"
echo "Logs: $ROOT/data/app.log"
