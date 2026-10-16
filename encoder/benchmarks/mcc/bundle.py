"""Collect the verified .gc files of all passing formulas into one directory with an index.

Usage: uv run python benchmarks/mcc/bundle.py --runs R1.csv.jsonl R2.csv.jsonl ... --emit DIR ... --out DIR
(later runs override earlier ones per formula, as in summarize.py)

The `spec:` of each file is replaced by the explicit Büchi automaton Spot derived from it (the spec
stays as a comment), so the bundled files parse without Spot and the ranking's automaton state
names cannot drift with the Spot version. Each rewritten file is checked to parse to the same
program, automaton and ranking as the original.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
from pathlib import Path

from summarize import load
from zkterm_tool.parser import parse_with_constants

README = """\
# MCC 2025 LTL formulas with verified ranking functions

Each `gc/<instance>-<exam>-<NN>.gc` is a self-contained zkmc input. It contains:

- the Petri net as guarded commands (one command per transition, places as integer variables) and
  its initial marking as `init:`;
- the MCC formula, converted from `LTLCardinality`/`LTLFireability`, as `ap` definitions plus
  `spec:` in comments;
- the Büchi automaton of `!(spec)` as `automaton_init:` and `trans`/`trans!` (fair) lines, as
  derived by Spot (`ltl2tgba -B`; version in the file);
- the `rank(q)` cases for every automaton state. Finite cases are guarded by the inductive
  invariant; everything else is `inf`.

Nets that can deadlock get extra stutter commands `[] <dead marking cube> -> p0 = p0`, because MCC
treats deadlocks as infinite stuttering.

All formulas are TRUE according to the MCC 2025 oracle. Every ranking was checked by
`zkterm_tool.verify_termination` (Farkas obligations) when it was emitted. `index.csv` lists the net
size, automaton size, number of ranking cases and number of obligations per file.

To re-check files, run this from `encoder/` (Spot is not needed):

    uv run zkverify path/to/gc/<file>.gc
    uv run python benchmarks/mcc/reverify.py path/to/gc   # all files, in parallel
"""


def spot_version() -> str:
    exe = os.environ.get("ZKTERM_LTL2TGBA", "ltl2tgba")
    return subprocess.run([exe, "--version"], capture_output=True, text=True, check=True).stdout.split("\n")[0]


def explicit_automaton(text: str, spot: str) -> str:
    resolved = parse_with_constants(text, resolve_ltl=True)
    automaton = [f"// Büchi automaton of !(spec) from {spot}, ltl2tgba -B",
                 f"automaton_init: {', '.join(resolved.automaton_initial_states)}",
                 *(str(t) for t in resolved.automaton_transitions), ""]
    lines = []
    for line in text.splitlines():
        if line.startswith(("ap ", "spec:")):
            line = "// " + line
        if automaton and line.startswith("rank("):
            lines += automaton
            automaton = None
        lines.append(line)
    out = "\n".join(lines) + "\n"
    explicit = parse_with_constants(out)
    for field in ("constants", "types", "init_condition", "commands", "ranking_functions",
                  "automaton_transitions", "automaton_initial_states"):
        if getattr(explicit, field) != getattr(resolved, field):
            raise ValueError(f"explicit automaton changes {field}")
    return out


FIELDS = ["places", "transitions", "aut_states", "aut_trans", "cases", "inf_cases", "obligations"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", type=Path, required=True)
    ap.add_argument("--deadlock-runs", nargs="*", type=Path, default=[],
                    help="Runs over nets with deadlocks (marks them in the index); also merged.")
    ap.add_argument("--emit", nargs="+", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    spot = spot_version()

    merged = {}
    for path in args.runs + args.deadlock_runs:
        for r in load(path):
            merged[f"{r['instance']}-{r['exam']}-{r['index']:02d}"] = r
    deadlocking = {r["instance"] for path in args.deadlock_runs for r in load(path)}
    files = {f.stem: f for d in args.emit for f in sorted(d.glob("*.gc"))}

    shutil.rmtree(args.out, ignore_errors=True)
    (args.out / "gc").mkdir(parents=True)
    rows, missing = [], []
    for k, r in sorted(merged.items()):
        if r["status"] != "pass":
            continue
        if k not in files:
            missing.append(k)
            continue
        (args.out / "gc" / f"{k}.gc").write_text(explicit_automaton(files[k].read_text(), spot))
        rows.append({"id": k, "model": r["instance"].split("-")[0], "instance": r["instance"],
                     "exam": r["exam"], "index": r["index"], "deadlocks": r["instance"] in deadlocking,
                     **{f: r[f] for f in FIELDS}})
    with open(args.out / "index.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (args.out / "README.md").write_text(README)
    print(f"{len(rows)} files in {args.out}" + (f"; {len(missing)} passing without a file: {missing}" if missing else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
