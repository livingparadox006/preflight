#!/usr/bin/env bash
# Stop the detached app. The shared llama-server is left running so it can be
# reused by the next launch (and by Foreshadow, which shares port 8081).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ -f data/app.pid ]]; then
  PID="$(cat data/app.pid)"
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    sleep 1
    if kill -0 "$PID" 2>/dev/null; then kill -9 "$PID" 2>/dev/null || true; fi
    echo "Preflight Companion stopped."
  else
    echo "No running Preflight process for pid $PID."
  fi
  rm -f data/app.pid
else
  echo "No data/app.pid found."
fi
