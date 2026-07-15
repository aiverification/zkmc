#!/usr/bin/env python3
"""Solve normalized ZKMC Farkas obligations with Z3."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from z3 import Int, Solver, Sum, sat
except ImportError as exc:
    raise SystemExit(
        "z3-solver is missing; run ./scripts/setup_ubuntu.sh first"
    ) from exc


def rectangular(name: str, matrix: list[list[int]]) -> int:
    """Validate rectangular shape and return columns."""
    if not matrix or not matrix[0]:
        raise ValueError(f"{name} must be non-empty")
    columns = len(matrix[0])
    if any(len(row) != columns for row in matrix):
        raise ValueError(f"{name} must be rectangular")
    return columns


def solve_one(item: dict[str, Any], bound: int, index: int) -> None:
    """Solve one bounded integer Farkas witness."""
    a_s = item["a_s"]
    b_s = item["b_s"]
    g_p = item["g_p"]
    h_p = item["h_p"]
    columns = rectangular("a_s", a_s)
    if rectangular("g_p", g_p) != columns:
        raise ValueError("a_s and g_p column counts differ")
    if len(b_s) != len(a_s) or len(h_p) != len(g_p):
        raise ValueError("matrix and vector dimensions differ")

    solver = Solver()
    lam = [Int(f"lambda_{index}_{row}") for row in range(len(a_s))]
    mu = [Int(f"mu_{index}_{row}") for row in range(len(g_p))]
    for value in [*lam, *mu]:
        solver.add(value >= 0, value <= bound)
    for column in range(columns):
        left = Sum([a_s[row][column] * lam[row] for row in range(len(a_s))])
        right = -Sum([g_p[row][column] * mu[row] for row in range(len(g_p))])
        solver.add(left == right)
    delta = (
        -Sum([b_s[row] * lam[row] for row in range(len(a_s))])
        - Sum([h_p[row] * mu[row] for row in range(len(g_p))])
        - 1
    )
    solver.add(delta >= 0, delta <= bound)
    if solver.check() != sat:
        raise ValueError(f"no bounded integer Farkas witness for {item['label']}")
    model = solver.model()
    item["lambda"] = [model.eval(value).as_long() for value in lam]
    item["mu"] = [model.eval(value).as_long() for value in mu]


def parse_args() -> argparse.Namespace:
    """Parse input and output JSON paths."""
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    return parser.parse_args()


def main() -> None:
    """Solve every obligation and write JSON."""
    args = parse_args()
    data = json.loads(args.input.read_text())
    if data.get("schema_version") != 1:
        raise ValueError("schema_version must equal 1")
    bound = int(data["bound"])
    if bound <= 0:
        raise ValueError("bound must be positive")
    for index, item in enumerate(data["obligations"]):
        solve_one(item, bound, index)
    args.output.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {len(data['obligations'])} solved obligations to {args.output}")


if __name__ == "__main__":
    main()
