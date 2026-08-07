#!/usr/bin/env bash
# Backward-compatible wrapper for the official exb_i1a2 benchmark.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
source "$HOME/.cargo/env"
./scripts/setup_benchmark_env.sh
.venv/bin/python scripts/run_benchmark.py exb_i1a2 --repeat 1

echo "EXB I1A2 COMPLETE"
echo "Metrics: $ROOT/artifacts/benchmarks/exb_i1a2/run_01/metrics.json"
