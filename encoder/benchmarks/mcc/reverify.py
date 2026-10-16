"""Re-check emitted .gc files (program + LTL spec + rankings) from disk.

Usage: uv run python benchmarks/mcc/reverify.py FILE_OR_DIR ... [--parse-only] [--limit N]
                                                [--jobs J] [--timeout S] [--mem-limit-mb M] [--out CSV]
"""

from __future__ import annotations

import argparse
import csv
import multiprocessing as mp
import random
import time
from pathlib import Path


def _check(path: str, parse_only: bool, mem_limit_mb: int, q) -> None:
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (mem_limit_mb << 20, resource.RLIM_INFINITY))
    from zkterm_tool import verify_termination
    from zkterm_tool.parser import parse_with_constants

    row = {"file": path, "status": "error", "obligations": "", "seconds": ""}
    t0 = time.time()
    try:
        result = parse_with_constants(Path(path).read_text(), resolve_ltl=True)
        if not result.ranking_functions:
            row["status"] = "no-ranking"
        elif parse_only:
            row["status"] = "parsed"
        else:
            v = verify_termination(result)
            row["status"] = "pass" if v.passed else "fail"
            row["obligations"] = len(v.obligations)
    except MemoryError:
        row["status"] = "memory"
    except Exception as e:  # noqa: BLE001
        row["status"] = f"error: {type(e).__name__}: {str(e).splitlines()[0][:120] if str(e) else ''}"
    row["seconds"] = round(time.time() - t0, 2)
    resource.setrlimit(resource.RLIMIT_AS, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    q.put(row)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--parse-only", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="Check a random sample of this many files.")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=1200.0)
    ap.add_argument("--mem-limit-mb", type=int, default=3000)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    import zkterm_tool.parser  # noqa: F401  (import once; forked workers inherit it)

    files = sorted(str(f) for p in args.paths for f in (p.glob("*.gc") if p.is_dir() else [p]))
    if args.limit:
        files = sorted(random.Random(0).sample(files, min(args.limit, len(files))))
    rows, running, todo, shown = [], {}, list(files), 0
    q = mp.Queue()
    while todo or running:
        while todo and len(running) < args.jobs:
            f = todo.pop()
            p = mp.Process(target=_check, args=(f, args.parse_only, args.mem_limit_mb, q))
            p.start()
            running[f] = (p, time.time())
        time.sleep(0.05)
        while not q.empty():
            row = q.get()
            rows.append(row)
            running.pop(row["file"])[0].join()
        for f, (p, t0) in list(running.items()):
            if time.time() - t0 > args.timeout:
                p.kill()
                running.pop(f)
                rows.append({"file": f, "status": "timeout", "obligations": "", "seconds": args.timeout})
            elif not p.is_alive() and p.exitcode != 0:
                p.join()
                running.pop(f)
                rows.append({"file": f, "status": f"crashed ({p.exitcode})", "obligations": "", "seconds": ""})
        if len(rows) >= shown + 100:
            shown = len(rows)
            print(f"  ... {len(rows)}/{len(files)}", flush=True)

    from collections import Counter
    print(f"{len(rows)} files:", dict(Counter(r["status"] for r in rows)))
    for r in rows:
        if r["status"] not in ("pass", "parsed"):
            print(f"  {r['status']:20} {r['file']}")
    if args.out:
        with open(args.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["file", "status", "obligations", "seconds"])
            w.writeheader()
            w.writerows(sorted(rows, key=lambda r: r["file"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
