#!/usr/bin/env python3
"""Combine all raw benchmark metrics into plot-ready CSV and JSONL files."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULT_ROOT = ROOT / "artifacts" / "benchmarks"
CSV_PATH = RESULT_ROOT / "benchmark_results.csv"
JSONL_PATH = RESULT_ROOT / "benchmark_results.jsonl"
EXCLUDED = {"plain_check_seconds", "circuit_check_seconds"}

PREFERRED = [
    "benchmark",
    "family",
    "repeat",
    "status",
    "error",
    "last_completed_phase",
    "obligation_count",
    "expected_obligation_count",
    "paper_state_space_log2",
    "initial_delay",
    "max_attempts",
    "dhcp_w1",
    "dhcp_w2",
    "no_offered_state",
    "init_count",
    "step_count",
    "fair_count",
    "max_secret_rows",
    "max_public_rows",
    "max_columns",
    "range_bits",
    "count_bits",
    "bound",
    "farkas_seconds",
    "adapt_seconds",
    "cargo_build_seconds",
    "nova_setup_seconds",
    "nova_fold_total_seconds",
    "nova_step_mean_ms",
    "nova_step_median_ms",
    "nova_step_p95_ms",
    "nova_step_max_ms",
    "nova_verify_seconds",
    "decider_setup_seconds",
    "decider_prove_seconds",
    "in_memory_verify_seconds",
    "serialization_seconds",
    "serialized_verify_seconds",
    "standalone_verify_seconds",
    "setup_seconds",
    "prover_seconds",
    "verifier_seconds",
    "total_pipeline_seconds",
    "statement_bytes",
    "proof_bytes",
    "verifier_parameter_bytes",
    "public_input_bytes",
    "manifest_bytes",
    "total_artifact_bytes",
    "peak_rss_kb",
]


def scalar(value: Any) -> bool:
    """Return whether a value belongs in a flat CSV row."""
    return value is None or isinstance(value, (str, int, float, bool))


def main() -> None:
    """Regenerate aggregate files from immutable per-run JSON files."""
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(RESULT_ROOT.glob("*/run_*/metrics.json")):
        row = json.loads(path.read_text())
        row["metrics_path"] = str(path.relative_to(ROOT))
        rows.append(
            {
                key: value
                for key, value in row.items()
                if scalar(value) and key not in EXCLUDED
            }
        )

    rows.sort(key=lambda row: (str(row.get("benchmark")), int(row.get("repeat", 0))))
    all_keys = {key for row in rows for key in row}
    fields = [key for key in PREFERRED if key in all_keys]
    fields.extend(sorted(all_keys - set(fields)))

    with CSV_PATH.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    with JSONL_PATH.open("w") as file:
        for row in rows:
            file.write(json.dumps(row, sort_keys=True) + "\n")

    print(f"aggregate rows: {len(rows)}")
    print(f"CSV: {CSV_PATH}")
    print(f"JSONL: {JSONL_PATH}")


if __name__ == "__main__":
    main()
