#!/usr/bin/env python3
"""Run only the six selected EXB and two smallest DHCP benchmarks."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "benchmarks" / "selected_benchmarks.json"


def main() -> None:
    """Run the selected cases sequentially and retain partial failures."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--skip", action="append", default=[])
    args = parser.parse_args()

    cases = json.loads(CONFIG.read_text())
    failures: list[str] = []
    for case in cases:
        name = case["name"]
        if name in args.skip:
            print(f"SKIP {name}")
            continue
        print(f"\n===== RUNNING {name} =====", flush=True)
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "run_benchmark.py"),
                name,
                "--repeat",
                str(args.repeat),
            ],
            cwd=ROOT,
            check=False,
        )
        if completed.returncode != 0:
            failures.append(name)

    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "aggregate_metrics.py")],
        cwd=ROOT,
        check=True,
    )
    if failures:
        print(f"FAILED BENCHMARKS: {', '.join(failures)}", file=sys.stderr)
        raise SystemExit(1)
    print("SELECTED BENCHMARKS COMPLETE")


if __name__ == "__main__":
    main()
