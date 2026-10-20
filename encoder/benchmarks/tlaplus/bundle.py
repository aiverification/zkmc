"""Collect the verified `.gc` files of all passing TLA+ instances into one directory with an index.

Usage: uv run python benchmarks/tlaplus/bundle.py --work benchmarks/corpus/tlaplus --out benchmarks/tlaplus/rankings

The files already contain their Büchi automaton explicitly, so they parse without Spot; each one
is re-parsed here to make sure it is self-contained.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

from zkterm_tool.parser import parse_with_constants

README = """\
# TLA+ Examples with verified ranking functions

Each `gc/<spec>__<model>__<property>.gc` is a self-contained zkmc input for one property of one
finite TLC model from the [TLA+ Examples](https://github.com/tlaplus/Examples) repository
(translated by `benchmarks/tlaplus/tla2gc.py`). It contains:

- the model as guarded commands over integer slots (the slot layout and the value tables of
  enumeration slots are listed in the header comment), with `type` ranges and `init:`; an
  unconditional stutter command, because TLA+ behaviours may stutter;
- for temporal properties under fairness, the slot `act` recording which fairness-relevant
  action the last step took;
- the property: an `INVARIANT` p as `G p`, or a temporal `PROPERTY` (or one top-level conjunct of
  it) with the specification's fairness as assumption, as LTL over named state predicates in
  comments, and the Büchi automaton of its negation (Spot `ltl2tgba -B`) as `automaton_init:`
  and `trans`/`trans!` (fair) lines;
- the `rank(q)` cases for every automaton state. Finite cases are guarded by an inductive
  invariant; everything else is `inf`.

TLC verifies every model, and the translation reaches exactly TLC's reachable states. Every
ranking was checked by `zkterm_tool.verify_termination` (Farkas obligations) when it was emitted.
`index.csv` lists the model, property, program size, automaton size, number of ranking cases and
number of obligations per file.

To re-check files, run this from `encoder/` (Spot is not needed):

    uv run zkverify path/to/gc/<file>.gc
    uv run python benchmarks/mcc/reverify.py path/to/gc   # all files, in parallel
"""

FIELDS = ["commands", "aut_states", "aut_trans", "cases", "inf_cases", "obligations"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, default=Path("benchmarks/corpus/tlaplus"))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    instances = {}
    with open(args.work / "instances.csv") as fh:
        for r in csv.DictReader(fh):
            instances[r["instance"]] = r
    models = {}
    with open(args.work / "models.csv") as fh:
        for r in csv.DictReader(fh):
            models[r["model"]] = r
    results = {}
    for line in (args.work / "prove.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            results[r["instance"]] = r

    shutil.rmtree(args.out, ignore_errors=True)
    (args.out / "gc").mkdir(parents=True)
    rows = []
    for name, r in sorted(results.items()):
        if r.get("status") != "pass":
            continue
        src = args.work / "rankings" / f"{name}.gc"
        text = src.read_text()
        parse_with_constants(text)  # self-contained: no Spot, no missing pieces
        (args.out / "gc" / src.name).write_text(text)
        inst = instances[name]
        m = models[inst["model"]]
        rows.append({"file": src.name, "spec": m["spec"], "model": inst["model"], "kind": inst["kind"],
                     "property": inst["part"] or inst["property"], "tlc_states": m["tlc_states"],
                     "slots": m["slots"], "fairness": inst.get("fairness", ""),
                     **{f: r.get(f, "") for f in FIELDS}})
    with open(args.out / "index.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (args.out / "README.md").write_text(README)
    print(f"{len(rows)} files in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
