"""Diagnose one MCC formula: time each phase and log every partition the synthesizer tries.

Usage: uv run python benchmarks/mcc/diag.py <instance> <LTLCardinality|LTLFireability> <index>
       [--explicit] [--budget SECONDS] [--dump FILE.gc]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from invariants import houdini  # noqa: E402
from mcc2gc import net_to_gc, parse_pnml, parse_properties, read_instance  # noqa: E402
from zkterm_tool import synth  # noqa: E402
from zkterm_tool.parser import parse_with_constants  # noqa: E402

CORPUS = Path(__file__).resolve().parents[1] / "corpus" / "mcc2025"


def fmt(c) -> str:
    return f"{c.left} {c.op.value} {c.right}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("instance")
    ap.add_argument("exam")
    ap.add_argument("index", type=int)
    ap.add_argument("--explicit", action="store_true")
    ap.add_argument("--budget", type=float, default=120)
    ap.add_argument("--dump", default=None)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--samples", type=int, default=0, help="Sample budget for invariant guesses.")
    ap.add_argument("--minimize", action="store_true", help="Shrink invariants to the needed atoms.")
    ap.add_argument("--rational", action="store_true", help="Synthesize via the real LP, then scale.")
    args = ap.parse_args()

    files = read_instance(CORPUS / "INPUTS-2025" / f"{args.instance}.tgz")
    net = parse_pnml(files["model.pnml"])
    prop = parse_properties(files[f"{args.exam}.xml"])[args.index]
    gc = net_to_gc(net, prop, None, args.instance)
    if args.dump:
        Path(args.dump).write_text(gc)
    print(f"net: {len(net.places)} places, {len(net.transitions)} transitions")
    print("spec:", gc.split('spec: "')[1].split('"')[0])

    t0 = time.time()
    result = parse_with_constants(gc, resolve_ltl=True)
    states = sorted({t.from_state for t in result.automaton_transitions}
                    | {t.to_state for t in result.automaton_transitions})
    print(f"spot: {time.time() - t0:.1f}s, {len(states)} states, "
          f"{len(result.automaton_transitions)} transitions "
          f"({sum(t.is_fair for t in result.automaton_transitions)} fair), init {result.automaton_initial_states}")
    for t in result.automaton_transitions:
        print(f"   {'!' if t.is_fair else ' '} {t.from_state}->{t.to_state}: "
              + (" && ".join(fmt(g) for g in t.guards) or "true"))

    if args.explicit:
        from explicit_check import check
        t0 = time.time()
        ex = check(result, budget=3_000_000, keep_rank=True)
        print(f"explicit: {time.time() - t0:.1f}s holds={ex.holds} product={ex.product_states} "
              f"markings={ex.markings} max_rank={ex.max_rank} distinct={ex.distinct_ranks}")
        if ex.holds:
            per_q = {}
            for (q, x), r in ex.rank.items():
                per_q.setdefault(q, set()).add(r)
            for q in sorted(per_q):
                print(f"   {q}: {len(per_q[q])} distinct rank values, max {max(per_q[q])}")

    t0 = time.time()
    inv = houdini(result, time_budget=args.budget, sample_budget=args.samples)
    print(f"houdini: {time.time() - t0:.1f}s")
    for q in sorted(inv):
        print(f"   I_{q} ({len(inv[q])}): " + " && ".join(fmt(c) for c in inv[q]))

    # Log every partition attempt.
    orig = synth._solve_partition

    def logged(result_, variables, states_, fixed, prog_encs, regions, region_guards, *a, **kw):
        t = time.time()
        out = orig(result_, variables, states_, fixed, prog_encs, regions, region_guards, *a, **kw)
        modes = sorted({v for r in regions for v in r})
        live = sum(g is not None for q in region_guards for g in region_guards[q])
        print(f"   partition {modes or '[]'}: {len(regions)} regions, {live} live (q,region) pairs, "
              f"{time.time() - t:.1f}s -> {'feasible' if out is not None else 'infeasible'}", flush=True)
        return out

    synth._solve_partition = logged
    t0 = time.time()
    try:
        synth.synthesize_into(result, max_mode_vars=1, time_budget=args.budget, invariants=inv,
                              rational=args.rational)
        print(f"synth: {time.time() - t0:.1f}s OK")
        for q, rf in sorted(result.ranking_functions.items()):
            for c in rf.cases:
                if not c.is_infinity:
                    print(f"   rank({q}): {c.expression}")
    except synth.SynthesisError as e:
        print(f"synth: {time.time() - t0:.1f}s FAILED: {e}")
        return 1
    if args.minimize:
        from invariants import needed_atoms
        t0 = time.time()
        small = needed_atoms(result, inv, result.ranking_functions)
        print(f"needed atoms: {time.time() - t0:.1f}s, "
              f"{sum(len(c) for c in inv.values())} -> {sum(len(c) for c in small.values())} atoms")
        for q in sorted(small):
            print(f"   N_{q} ({len(small[q])}): " + " && ".join(fmt(c) for c in small[q]))
        result.ranking_functions = {}
        t0 = time.time()
        synth.synthesize_into(result, max_mode_vars=1, time_budget=args.budget, invariants=small,
                              rational=args.rational)
        print(f"re-synth: {time.time() - t0:.1f}s OK")
    if args.verify:
        from zkterm_tool import verify_termination
        t0 = time.time()
        v = verify_termination(result)
        print(f"verify: {time.time() - t0:.1f}s passed={v.passed} obligations={len(v.obligations)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
