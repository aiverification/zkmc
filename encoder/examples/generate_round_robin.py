#!/usr/bin/env python3
"""Generate a round-robin lock .gc file for k parties.

Usage:
    python examples/generate_round_robin.py K [-o OUTFILE]

Produces a .gc file for a k-process round-robin mutex with the same shape
as examples/round-robin.gc (which is the k=3 instance).
"""

import argparse
import sys


def _valid_guard(k: int, j: int) -> str:
    return " && ".join(f"state{m} <= {2 if m == j else 1}" for m in range(k))


def _inf_cases(k: int) -> list[str]:
    lines = [
        "    [] turn < 0 -> inf",
        f"    [] turn > {k - 1} -> inf",
    ]
    for j in range(k):
        prefix_bounds = []
        for m in range(k):
            bound = 2 if m == j else 1
            prefix = "".join(f" && state{mm} <= {bm}" for mm, bm in prefix_bounds)
            lines.append(f"    [] turn == {j}{prefix} && state{m} > {bound} -> inf")
            prefix_bounds.append((m, bound))
    return lines


def generate(k: int) -> str:
    if k < 2:
        raise ValueError("k must be >= 2")

    out: list[str] = []

    out.append("// Round Robin Lock")
    out.append(
        "// N processes share a lock; turns are granted in strict cyclic order "
        "0,1,2,...,N-1,0,..."
    )
    out.append("")
    out.append("// Constants for process state")
    out.append("const idle = 0        // not interested in the lock right now")
    out.append("const waiting = 1     // wants the lock, waiting for its turn")
    out.append("const holding = 2     // currently holds the lock")
    out.append("")
    out.append("// Scheduler parameters")
    out.append(f"const numProcs = {k}    // number of processes")
    out.append("")
    out.append("// Variables")
    out.append("type turn: 0..numProcs - 1      // whose turn it currently is")
    for i in range(k):
        decl = f"type state{i}: 0..2"
        out.append(f"{decl:<32}// state of process {i}")
    out.append("")

    init_clauses = ["turn == 0"] + [f"state{i} == idle" for i in range(k)]
    out.append("// Initially: it's process 0's turn, everyone idle")
    out.append("init: " + " && ".join(init_clauses))
    out.append("")

    out.append("// ----- Transitions -----")
    out.append("")
    for i in range(k):
        out.append(f"// --- Process {i} ---")
        if i == 0:
            out.append("// Become interested in acquiring the lock")
            out.append(f"[] state{i} == idle -> state{i} = waiting")
            out.append("")
            out.append("// Acquire the lock: it's our turn and we're waiting")
            out.append(f"[] state{i} == waiting && turn == {i} -> state{i} = holding")
            out.append("")
            out.append("// Release the lock and pass the turn to the next process")
            out.append(
                f"[] state{i} == holding -> state{i} = idle; turn = {(i + 1) % k}"
            )
        else:
            out.append(f"[] state{i} == idle -> state{i} = waiting")
            out.append(f"[] state{i} == waiting && turn == {i} -> state{i} = holding")
            out.append(
                f"[] state{i} == holding -> state{i} = idle; turn = {(i + 1) % k}"
            )
        out.append("")

    property_terms = " /\\ ".join(f"G(s{i}w -> F s{i}h)" for i in range(k))
    out.append(f"// Property: {property_terms}")
    out.append("automaton_init: q0")
    out.append("trans(q0, q0): true")
    for i in range(k):
        q = i + 1
        out.append(f"trans(q0, q{q}): state{i} == waiting")
        out.append(f"trans!(q{q}, q{q}): state{i} == idle")
        out.append(f"trans!(q{q}, q{q}): state{i} == waiting")
    out.append("")

    out.append("// ----- Ranking Functions -----")
    out.append("// Invariant: state_i == holding (= 2) implies turn == i.")
    out.append(
        "// Finite case upper bounds encode the invariant; infinity ladder per turn handles"
    )
    out.append(
        "// everything else (out-of-range + invariant violations in one shot via state_j > upper)."
    )
    out.append(
        "// Lower bounds on state_i are unnecessary: negative state_i keeps rank strictly positive."
    )
    out.append("")

    state_sum = " - ".join(f"state{m}" for m in range(k))

    # rank(q0): constant 3k+3 per valid turn region, then inf cases.
    q0_const = 3 * k + 3
    out.append("rank(q0):")
    for j in range(k):
        out.append(f"    [] turn == {j} && {_valid_guard(k, j)} -> {q0_const}")
    out.extend(_inf_cases(k))
    out.append("")

    # rank(q_{i+1}) for each tracked process i.
    for i in range(k):
        out.append(f"rank(q{i + 1}):")
        for t_offset in range(k):
            j = (i + t_offset) % k
            distance = (i - j) % k
            base = 6 + 3 * distance
            out.append(
                f"    [] turn == {j} && {_valid_guard(k, j)} -> -{state_sum} + {base}"
            )
        out.extend(_inf_cases(k))
        out.append("")

    # Drop trailing blank so file ends with a single newline.
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("k", type=int, help="Number of processes (>= 2)")
    parser.add_argument(
        "-o", "--output", help="Output file path (default: stdout)", default=None
    )
    args = parser.parse_args()

    if args.k < 2:
        print("error: k must be >= 2", file=sys.stderr)
        return 1

    content = generate(args.k)
    if args.output:
        with open(args.output, "w") as f:
            f.write(content)
    else:
        sys.stdout.write(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
