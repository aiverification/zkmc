#!/usr/bin/env python3
"""Normalize and independently check official ZKMC Farkas obligations."""

from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path
from typing import Any

MAX_U64_BITS = 63


def flatten_column(values: list[list[int]]) -> list[int]:
    """Flatten one-column vectors from official JSON."""
    return [int(row[0]) for row in values]


def dot(left: list[int], right: list[int]) -> int:
    """Compute one exact integer dot product."""
    return sum(a * b for a, b in zip(left, right, strict=True))


def transpose_product(matrix: list[list[int]], vector: list[int]) -> list[int]:
    """Compute an exact transposed matrix product."""
    if not matrix:
        return []
    return [dot([row[column] for row in matrix], vector) for column in range(len(matrix[0]))]


def classify(item: dict[str, Any]) -> str:
    """Map official obligation types into backend kinds."""
    obligation_type = item["obligation_type"]
    if obligation_type == "initial_non_infinity":
        return "init"
    if obligation_type == "transition_non_infinity":
        return "step"
    if obligation_type == "update":
        return "fair" if item.get("is_fair", False) else "step"
    raise ValueError(f"unsupported obligation type: {obligation_type}")


def label(index: int, item: dict[str, Any]) -> str:
    """Build a stable human-readable obligation label."""
    parts = [f"official-{index:05}", item["obligation_type"]]
    if "program_transition" in item:
        parts.append(f"program={item['program_transition']}")
    automaton = item.get("automaton_transition")
    if automaton:
        parts.append(f"automaton={automaton['from']}->{automaton['to']}")
    if item.get("is_fair", False):
        parts.append("fair")
    return " ".join(parts)


def check_rectangular(name: str, matrix: list[list[int]]) -> int:
    """Validate a non-empty rectangular matrix."""
    if not matrix or not matrix[0]:
        raise ValueError(f"{name} must be non-empty")
    columns = len(matrix[0])
    if any(len(row) != columns for row in matrix):
        raise ValueError(f"{name} must be rectangular")
    return columns


def adapt_item(index: int, item: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Adapt and independently verify one obligation."""
    if not item.get("satisfiable", False) or item.get("witness") is None:
        raise ValueError(f"obligation {index} lacks a Farkas witness")

    matrices = item["matrices"]
    witness = item["witness"]
    a_s = [[int(value) for value in row] for row in matrices["A_s"]]
    b_s = flatten_column(matrices["b_s"])
    g_p = [[int(value) for value in row] for row in matrices["G_p"]]
    h_p = flatten_column(matrices["h_p"])
    lambda_s = flatten_column(witness["lambda_s"])
    mu_s = flatten_column(witness["mu_s"])

    a_columns = check_rectangular("A_s", a_s)
    g_columns = check_rectangular("G_p", g_p)
    if a_columns != g_columns:
        raise ValueError(f"obligation {index} has mismatched columns")
    if len(b_s) != len(a_s) or len(lambda_s) != len(a_s):
        raise ValueError(f"obligation {index} has mismatched secret dimensions")
    if len(h_p) != len(g_p) or len(mu_s) != len(g_p):
        raise ValueError(f"obligation {index} has mismatched public dimensions")
    if any(value < 0 for value in lambda_s + mu_s):
        raise ValueError(f"obligation {index} has a negative multiplier")

    secret_product = transpose_product(a_s, lambda_s)
    public_product = transpose_product(g_p, mu_s)
    if any(left != -right for left, right in zip(secret_product, public_product, strict=True)):
        raise ValueError(f"obligation {index} fails vector equality")

    delta = -dot(b_s, lambda_s) - dot(h_p, mu_s) - 1
    if delta < 0:
        raise ValueError(f"obligation {index} has negative delta {delta}")

    magnitudes = [
        *(abs(value) for row in a_s for value in row),
        *(abs(value) for value in b_s),
        *(abs(value) for row in g_p for value in row),
        *(abs(value) for value in h_p),
        *lambda_s,
        *mu_s,
        delta,
    ]
    adapted = {
        "kind": classify(item),
        "label": label(index, item),
        "a_s": a_s,
        "b_s": b_s,
        "g_p": g_p,
        "h_p": h_p,
        "lambda": lambda_s,
        "mu": mu_s,
    }
    return adapted, max(magnitudes, default=1)


def write_config(
    path: Path,
    secret_rows: int,
    public_rows: int,
    columns: int,
    bound: int,
    count: int,
) -> None:
    """Write exact fixed dimensions for this benchmark."""
    range_bits = max(1, bound.bit_length())
    count_bits = max(1, count.bit_length())
    if range_bits > MAX_U64_BITS:
        raise ValueError(f"required range width {range_bits} exceeds {MAX_U64_BITS} bits")
    text = (
        "// Stores generated benchmark circuit dimensions.\n\n"
        f"pub const MAX_SECRET_ROWS: usize = {secret_rows};\n"
        f"pub const MAX_PUBLIC_ROWS: usize = {public_rows};\n"
        f"pub const MAX_COLUMNS: usize = {columns};\n"
        f"pub const RANGE_BITS: usize = {range_bits};\n"
        f"pub const COUNT_BITS: usize = {count_bits};\n"
    )
    path.write_text(text)


def main() -> None:
    """Convert, verify, and size one official benchmark."""
    parser = argparse.ArgumentParser()
    parser.add_argument("official_json", type=Path)
    parser.add_argument("batch_json", type=Path)
    parser.add_argument("config_rs", type=Path)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--expected-count", type=int)
    args = parser.parse_args()

    official = json.loads(args.official_json.read_text())
    obligations = official.get("obligations", [])
    declared_count = int(official.get("count", -1))
    if declared_count != len(obligations):
        raise ValueError("official count does not match obligation list")
    if args.expected_count is not None and declared_count != args.expected_count:
        raise ValueError(
            f"expected {args.expected_count} obligations, received {declared_count}"
        )

    adapted: list[dict[str, Any]] = []
    bound = declared_count
    max_secret_rows = 0
    max_public_rows = 0
    max_columns = 0
    for index, item in enumerate(obligations):
        converted, item_maximum = adapt_item(index, item)
        adapted.append(converted)
        bound = max(bound, item_maximum)
        max_secret_rows = max(max_secret_rows, len(converted["a_s"]))
        max_public_rows = max(max_public_rows, len(converted["g_p"]))
        max_columns = max(max_columns, len(converted["a_s"][0]))

    blinding_low = secrets.randbits(64)
    blinding_high = secrets.randbits(64)
    if blinding_low == 0 and blinding_high == 0:
        blinding_high = 1

    batch = {
        "schema_version": 3,
        "benchmark": args.benchmark,
        "bound": bound,
        "model_blinding": {"low": blinding_low, "high": blinding_high},
        "obligations": adapted,
    }
    args.batch_json.parent.mkdir(parents=True, exist_ok=True)
    args.batch_json.write_text(json.dumps(batch, indent=2) + "\n")
    write_config(
        args.config_rs,
        max_secret_rows,
        max_public_rows,
        max_columns,
        bound,
        declared_count,
    )

    kinds = {kind: sum(item["kind"] == kind for item in adapted) for kind in ("init", "step", "fair")}
    print(f"official obligations: {declared_count}")
    print(f"kinds: {kinds}")
    print(f"fixed shape: ({max_secret_rows},{max_public_rows},{max_columns})")
    print(f"inclusive bound: {bound}")
    print(f"range bits: {max(1, bound.bit_length())}")
    print(f"count bits: {max(1, declared_count.bit_length())}")
    print("model blinding: generated 128 secret bits")


if __name__ == "__main__":
    main()
