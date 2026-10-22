#!/usr/bin/env bash
# Complete standalone verification, then collect the selected benchmark dataset.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
source "$HOME/.cargo/env"

mkdir -p artifacts/benchmarks
./scripts/setup_benchmark_env.sh
./scripts/check_structure.sh
cargo fmt
cargo fmt --check
cargo test --locked -- --nocapture

# First produce a real proof bundle, then test all rejection paths against it.
.venv/bin/python scripts/run_benchmark.py exb_i1a2 --repeat 1
.venv/bin/python scripts/test_verification.py \
  --binary target/release/zkmc \
  --artifacts artifacts/benchmarks/exb_i1a2/run_01/proof \
  --statement artifacts/benchmarks/exb_i1a2/run_01/statement.json \
  --trusted-verifier artifacts/benchmarks/trusted_verifiers/exb_i1a2.bin

# Run only the remaining five EXB and two smallest DHCP cases.
.venv/bin/python scripts/run_selected_benchmarks.py \
  --repeat 1 \
  --skip exb_i1a2

.venv/bin/python scripts/aggregate_metrics.py
.venv/bin/python - <<'PY'
import csv
from pathlib import Path

path = Path("artifacts/benchmarks/benchmark_results.csv")
rows = list(csv.DictReader(path.open()))
expected = {
    "exb_i1a2", "exb_i2a2", "exb_i4a2", "exb_i2a3",
    "exb_i8a2", "exb_i4a3", "dhcp_noOFF_7_2_7", "dhcp_7_2_7",
}
latest = {row["benchmark"]: row for row in rows if row.get("repeat") == "1"}
missing = expected - latest.keys()
failed = {name for name in expected if name in latest and latest[name].get("status") != "ok"}
if missing or failed:
    raise SystemExit(f"missing={sorted(missing)} failed={sorted(failed)}")
print("all 8 selected benchmark rows are complete")
PY

echo
echo "PHASE 4 AND SELECTED BENCHMARK COLLECTION COMPLETE"
echo "CSV: $ROOT/artifacts/benchmarks/benchmark_results.csv"
echo "JSONL: $ROOT/artifacts/benchmarks/benchmark_results.jsonl"
echo "Raw runs: $ROOT/artifacts/benchmarks/<benchmark>/run_01/"
