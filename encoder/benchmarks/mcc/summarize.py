"""Merge spike runs (later runs override earlier ones per formula) and summarize coverage.

Usage: uv run python benchmarks/mcc/summarize.py RUN.csv[.jsonl] [RUN2 ...] [--by-model] [--list-pass FILE]
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def load(path: Path) -> list[dict]:
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    with open(path) as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["index"] = int(r["index"])
        for k in ("obligations", "cases", "places", "transitions"):
            if r.get(k):
                r[k] = int(r[k])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--by-model", action="store_true")
    ap.add_argument("--list-pass", default=None, help="Write passing formula ids to this file.")
    args = ap.parse_args()

    merged: dict[tuple, dict] = {}
    for path in args.runs:
        for r in load(path):
            merged[(r["instance"], r["exam"], r["index"])] = r
    rows = list(merged.values())
    true_rows = [r for r in rows if r["oracle"] == "TRUE"]
    c = Counter(r["status"] for r in true_rows)
    n = len(true_rows)
    nontrivial = n - c.get("tautology", 0)
    print(f"{n} TRUE formulas, {len({r['instance'] for r in true_rows})} instances, "
          f"{len({r['instance'].split('-')[0] for r in true_rows})} models")
    for k, v in c.most_common():
        print(f"  {k:14} {v:5}  ({100 * v / n:.1f}%)")
    print(f"  pass rate excluding tautologies: {100 * c.get('pass', 0) / max(1, nontrivial):.1f}%")

    passed = [r for r in true_rows if r["status"] == "pass"]
    if passed:
        obl = [int(r["obligations"]) for r in passed]
        print(f"  obligations of passing: median {statistics.median(obl):.0f}, "
              f"p90 {statistics.quantiles(obl, n=10)[-1]:.0f}, max {max(obl)}; "
              f"<=1649 (DHCP): {sum(o <= 1649 for o in obl)}")
    unsound = [r for r in rows if r["oracle"] == "FALSE" and r.get("status") == "pass"]
    if any("status" in r for r in rows if r["oracle"] == "FALSE"):
        print(f"  FALSE formulas that passed (must be 0): {len(unsound)}")

    if args.by_model:
        per = defaultdict(Counter)
        for r in true_rows:
            per[r["instance"].split("-")[0]][r["status"]] += 1
        print(f"\n{'model':28}{'TRUE':>6}{'pass':>6}{'t/o':>5}{'other':>6}")
        for m in sorted(per, key=lambda m: -per[m]["pass"] / sum(per[m].values())):
            k = per[m]
            tot = sum(k.values())
            print(f"{m:28}{tot:6}{k['pass']:6}{k['timeout']:5}{tot - k['pass'] - k['timeout']:6}")

    if args.list_pass:
        Path(args.list_pass).write_text(
            "".join(f"{r['instance']}-{r['exam']}-{r['index']:02d}\n" for r in sorted(
                passed, key=lambda r: (r["instance"], r["exam"], r["index"]))))
        print(f"wrote {len(passed)} ids to {args.list_pass}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
