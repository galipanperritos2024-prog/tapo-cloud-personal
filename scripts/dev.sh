#!/usr/bin/env bash
# Development helper (Linux/macOS): backend + camera-service + MediaMTX + Next.js dev server.
#   ./scripts/dev.sh            # http://localhost:3000 (hot reload UI, API proxied to :8000)
#   ./scripts/dev.sh --fake     # also start a fake camera on rtsp://127.0.0.1:8654
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { python3 -m venv "$ROOT/.venv"; "$ROOT/.venv/bin/pip" install -e "$ROOT/backend[dev]"; }

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM

if [ "${1:-}" = "--fake" ]; then
  export TAPOCLOUD_ALLOW_PUBLIC_CAMERA_IPS=true   # the fake camera lives on 127.0.0.1
  "$PY" "$ROOT/scripts/fake_camera.py" & pids+=($!)
fi
"$PY" "$ROOT/scripts/run_all.py" & pids+=($!)
(
  cd "$ROOT/frontend"
  [ -d node_modules ] || npm ci
  npm run dev
) & pids+=($!)
wait
