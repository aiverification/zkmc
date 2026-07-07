"""Batch-run KoAT `.koat` termination benchmarks: import -> synthesize -> verify.

For each `.koat` file in a corpus directory (default: ../examples/its), import it into guarded
commands + the termination automaton, synthesize a ranking (symbolic, no bounds), and verify it.
Each file runs in a child process with a wall-clock timeout so a hard synthesis case can't stall
the sweep. Prints a per-file status and a summary tally.

Usage:
  uv run python benchmarks/run_its.py [--corpus DIR] [--timeout SECONDS]
"""

import argparse
import glob
import os
import sys
import time
from multiprocessing import Process, Queue


def _run_one(path: str, q: "Queue") -> None:
    try:
        from zkterm_tool.koat import import_koat_file
        from zkterm_tool.synth import synthesize_into, SynthesisError
        from zkterm_tool import verify_termination

        result = import_koat_file(path)
        try:
            synthesize_into(result)
        except SynthesisError as e:
            q.put(("no-ranking", str(e).splitlines()[0][:70]))
            return
        v = verify_termination(result)
        n_cases = sum(1 for c in result.ranking_functions["q0"].cases if not c.is_infinity)
        q.put(("pass" if v.passed else "verify-fail", f"{n_cases} cases, {len(v.obligations)} obl"))
    except Exception as e:  # unsupported (Com_n, non-linear) or parse error
        q.put(("unsupported", f"{type(e).__name__}: {str(e).splitlines()[0][:60]}"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Batch-run KoAT .koat termination benchmarks.")
    default_corpus = os.path.join(os.path.dirname(__file__), "..", "examples", "its")
    ap.add_argument("--corpus", default=default_corpus, help="Directory of .koat files.")
    ap.add_argument("--timeout", type=float, default=30.0, help="Per-file timeout in seconds.")
    args = ap.parse_args(argv)

    files = sorted(glob.glob(os.path.join(args.corpus, "*.koat")))
    if not files:
        print(f"No .koat files found in {args.corpus}", file=sys.stderr)
        return 1

    counts: dict[str, int] = {}
    for f in files:
        q: Queue = Queue()
        p = Process(target=_run_one, args=(f, q))
        t0 = time.time()
        p.start()
        p.join(args.timeout)
        if p.is_alive():
            p.terminate()
            p.join()
            status, detail = "timeout", f">{args.timeout:.0f}s"
        elif not q.empty():
            status, detail = q.get()
        else:
            status, detail = "error", "no result (crash?)"
        counts[status] = counts.get(status, 0) + 1
        print(f"  {os.path.basename(f):28} {status:12} {detail}  ({time.time() - t0:.1f}s)")

    print("-" * 60)
    total = sum(counts.values())
    print(f"  {total} files: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
