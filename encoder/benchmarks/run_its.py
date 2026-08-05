"""Batch-run KoAT `.koat` termination benchmarks and report coverage statistics.

For each `.koat` file: import -> synthesize a ranking (symbolic, no bounds) -> verify. Each file
runs in a child process with a wall-clock timeout (synthesis can be slow / hang on hard cases), and
its outcome is classified with a *reason*:

  pass                 ranking synthesized, validated (disjoint/covering/non-negative) and verified
  verify-fail          synthesized, but validation or the verifier rejected it
  no-ranking           search exhausted (single-scalar limit: needs multiphase / invariants)
  unsupported-comn     Com_n (n>1) recursion
  unsupported-nonlinear   non-linear update/guard
  unsupported-parse    could not parse / structural issue
  timeout              exceeded --timeout
  error                unexpected failure

Prints a per-file line, then a summary tally (overall + per top-level suite) and, with --out, a CSV.
With --emit-farkas DIR, passing files also get their Farkas-dual JSON dumped (input for zkmc-symbolic).

Usage:
  bash benchmarks/fetch_corpus.sh
  uv run python benchmarks/run_its.py --corpus benchmarks/corpus/Complexity_ITS --jobs 8 --timeout 20
"""

import argparse
import csv
import glob
import os
import sys
import time
from multiprocessing import Process, Queue


def _classify(path, max_mode_vars, max_regions, emit_dir):
    """Run one benchmark; return (status, detail, meta). Runs in a child process."""
    from lark.exceptions import LarkError
    from zkterm_tool.koat import import_koat_file
    from zkterm_tool.synth import synthesize_into, SynthesisError
    from zkterm_tool import verify_termination

    meta = {"locations": "", "transitions": "", "cases": "", "obligations": "", "synth_s": ""}

    # --- import / parse ---
    try:
        result = import_koat_file(path)
    except LarkError as e:
        return "unsupported-parse", str(e).splitlines()[0][:70], meta
    except ValueError as e:
        msg = str(e)
        if "recursion" in msg or "Com_n" in msg or "successors" in msg:
            return "unsupported-comn", msg.splitlines()[0][:70], meta
        if "on-linear" in msg:  # "Non-linear exponentiation ..."
            return "unsupported-nonlinear", msg.splitlines()[0][:70], meta
        return "unsupported-parse", msg.splitlines()[0][:70], meta

    pc = next(iter(result.types))  # the koat importer renames pc to _pc on collision
    meta["locations"] = result.types[pc].max_value + 1
    meta["transitions"] = len(result.commands)

    # --- synthesize ---
    t0 = time.time()
    try:
        synthesize_into(result, max_mode_vars=max_mode_vars, max_regions=max_regions)
    except SynthesisError:
        meta["synth_s"] = round(time.time() - t0, 2)
        return "no-ranking", "search exhausted (multiphase/invariant?)", meta
    except ValueError as e:
        if "on-linear" in str(e):  # "Non-linear multiplication/exponentiation"
            return "unsupported-nonlinear", str(e).splitlines()[0][:70], meta
        return "error", f"{type(e).__name__}: {str(e).splitlines()[0][:60]}", meta
    meta["synth_s"] = round(time.time() - t0, 2)

    # --- validate (well-formedness: disjointness, coverage, non-negativity) ---
    from zkterm_tool import validate_ranking_function
    from zkterm_tool.ranking_encoder import encode_ranking_functions
    try:
        for state, enc in encode_ranking_functions(result.ranking_functions).items():
            ok, errors = validate_ranking_function(enc.finite_cases, enc.infinity_cases, enc.variables)
            if not ok:
                return "verify-fail", f"invalid ranking ({state}): {errors[0][:45]}", meta
    except Exception as e:
        return "error", f"validate {type(e).__name__}: {str(e).splitlines()[0][:50]}", meta

    # --- verify ---
    try:
        v = verify_termination(result)
    except Exception as e:
        return "error", f"verify {type(e).__name__}: {str(e).splitlines()[0][:50]}", meta
    meta["cases"] = sum(1 for c in result.ranking_functions["q0"].cases if not c.is_infinity)
    meta["obligations"] = len(v.obligations)
    if not v.passed:
        return "verify-fail", f"{meta['obligations']} obl", meta

    if emit_dir:
        try:
            import json
            from zkterm_tool.farkas_cli import extract_farkas_obligations_from_result
            os.makedirs(emit_dir, exist_ok=True)
            obls = extract_farkas_obligations_from_result(result)  # keeps the synthesized ranking
            out = os.path.join(emit_dir, os.path.splitext(os.path.basename(path))[0] + ".json")
            with open(out, "w") as fh:
                json.dump({"obligations": obls, "count": len(obls)}, fh)
        except Exception as e:
            # Emission is best-effort, but never silent: a broken pipeline should be visible.
            print(f"  warning: --emit-farkas failed for {os.path.basename(path)}: {e}", file=sys.stderr)

    return "pass", f"{meta['cases']} cases, {meta['obligations']} obl", meta


def _worker(path, q, max_mode_vars, max_regions, emit_dir):
    try:
        q.put(_classify(path, max_mode_vars, max_regions, emit_dir))
    except Exception as e:  # pragma: no cover - defensive
        q.put(("error", f"{type(e).__name__}: {str(e)[:60]}", {}))


def _crossed_milestone(done: int, reported: int, step: int = 25) -> bool:
    """True when `done` entered a new `step` bucket since the last report (print each bucket once,
    even when several results land in one poll iteration or the count stalls on a multiple)."""
    return done // step > reported // step


def _suite_of(path, corpus):
    rel = os.path.relpath(path, corpus)
    parts = rel.split(os.sep)
    return parts[0] if len(parts) > 1 else "(root)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coverage statistics for KoAT .koat termination benchmarks.")
    ap.add_argument("--corpus", required=True, help="Directory of .koat files (searched recursively).")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--timeout", type=float, default=20.0, help="Per-file timeout (s).")
    ap.add_argument("--limit", type=int, default=None, help="Only run the first N files (sampling).")
    ap.add_argument("--max-mode-vars", type=int, default=2)
    ap.add_argument("--max-regions", type=int, default=64)
    ap.add_argument("--out", default=None, help="Write per-file results to this CSV.")
    ap.add_argument("--emit-farkas", default=None, metavar="DIR", help="Dump Farkas JSON for passing files.")
    ap.add_argument("--verbose", action="store_true", help="Print a line per file.")
    args = ap.parse_args(argv)

    files = sorted(glob.glob(os.path.join(args.corpus, "**", "*.koat"), recursive=True))
    if args.limit:
        files = files[: args.limit]
    if not files:
        print(f"No .koat files under {args.corpus}", file=sys.stderr)
        return 1

    results = []  # (path, suite, status, detail, meta)
    active = []   # (proc, q, path, t0)
    idx = 0
    t_start = time.time()

    def launch(path):
        q: Queue = Queue()
        p = Process(target=_worker, args=(path, q, args.max_mode_vars, args.max_regions, args.emit_farkas),
                    daemon=True)  # don't block interpreter exit (Ctrl-C) on a stuck synthesis
        p.start()
        active.append((p, q, path, time.time()))

    reported = 0
    while idx < len(files) or active:
        while len(active) < args.jobs and idx < len(files):
            launch(files[idx]); idx += 1
        time.sleep(0.05)
        still = []
        for p, q, path, t0 in active:
            if not p.is_alive():
                p.join()
                try:
                    status, detail, meta = q.get(timeout=1.0)
                except Exception:
                    status, detail, meta = "error", f"no result (exit code {p.exitcode})", {}
            elif time.time() - t0 > args.timeout:
                p.terminate(); p.join()
                # The child may have finished between the aliveness check and the kill; prefer its
                # real result over a timeout misclassification.
                try:
                    status, detail, meta = q.get(timeout=0.5)
                except Exception:
                    status, detail, meta = "timeout", f">{args.timeout:.0f}s", {}
            else:
                still.append((p, q, path, t0)); continue
            suite = _suite_of(path, args.corpus)
            results.append((path, suite, status, detail, meta))
            if args.verbose:
                print(f"  {os.path.basename(path):34} {status:20} {detail}")
        active = still
        if not args.verbose and _crossed_milestone(len(results), reported):
            reported = len(results)
            print(f"  ... {len(results)}/{len(files)} done", flush=True)

    # --- summary ---
    from collections import Counter, defaultdict
    overall = Counter(r[2] for r in results)
    per_suite = defaultdict(Counter)
    for _, suite, status, _, _ in results:
        per_suite[suite][status] += 1

    n = len(results)
    order = ["pass", "no-ranking", "timeout", "unsupported-parse", "unsupported-comn",
             "unsupported-nonlinear", "verify-fail", "error"]
    print("\n" + "=" * 64)
    print(f"{n} benchmarks  ({time.time() - t_start:.0f}s wall, jobs={args.jobs}, timeout={args.timeout:.0f}s)")
    for k in order:
        if overall.get(k):
            print(f"  {k:22} {overall[k]:5}  ({100 * overall[k] / n:.1f}%)")
    print("  per suite:")
    for suite in sorted(per_suite):
        c = per_suite[suite]
        tot = sum(c.values())
        print(f"    {suite:26} {c.get('pass', 0):4}/{tot:<4} pass"
              + (f"  no-ranking={c.get('no-ranking',0)} timeout={c.get('timeout',0)}"
                 f" unsupported={c.get('unsupported-parse',0)+c.get('unsupported-comn',0)+c.get('unsupported-nonlinear',0)}"))

    if args.out:
        with open(args.out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["file", "suite", "status", "detail", "locations", "transitions", "cases", "obligations", "synth_s"])
            for path, suite, status, detail, meta in results:
                w.writerow([os.path.relpath(path, args.corpus), suite, status, detail,
                            meta.get("locations", ""), meta.get("transitions", ""),
                            meta.get("cases", ""), meta.get("obligations", ""), meta.get("synth_s", "")])
        print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
