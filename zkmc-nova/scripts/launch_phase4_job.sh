#!/usr/bin/env bash
# Launch the long benchmark job in the background and preserve its PID/log.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/artifacts/benchmarks/phase4_benchmarks_job.log"
PID_FILE="$ROOT/artifacts/benchmarks/phase4_benchmarks_job.pid"

mkdir -p "$ROOT/artifacts/benchmarks"
cd "$ROOT"
nohup ./scripts/phase4_benchmarks_job.sh >"$LOG" 2>&1 &
echo $! | tee "$PID_FILE"
echo "job log: $LOG"
echo "follow with: tail -f $LOG"
