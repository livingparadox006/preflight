#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -x vendor/llama/llama-server ]]; then
  echo "The local runtime is missing. Expected vendor/llama/llama-server."
  echo "Link or copy a llama.cpp build into vendor/llama/."
  exit 1
fi

if ! find -L models -maxdepth 1 -name '*.gguf' -print -quit 2>/dev/null | grep -q .; then
  echo "No local model found. Expected a .gguf file under models/."
  exit 1
fi

mkdir -p data
exec python3 app/server.py "$@"
