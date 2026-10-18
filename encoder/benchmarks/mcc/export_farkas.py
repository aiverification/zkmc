"""Export verified .gc rankings as Farkas-dual JSON for the folding prover bench.

One worker per file (same Process/Queue pattern as `reverify.py`); per-worker RLIMIT_AS
for OOM isolation; timeout-kill; JSONL journal + resume on `pass` (plus `--retry-status`
and `--force` to override).

Selection filters compose (AND):

    [paths]              positional files/dirs; default benchmarks/mcc/rankings/gc
    --range START..END   half-open Rust-style slice of the canonical sorted list
                         (same sort the folding bench applies, so ranges align)
    --exams EXAM,...     keep only filenames containing one of the given exams
    --min/--max-obligations N   read from rankings/index.csv (zero parsing)
    --random N           deterministic random sample (seed 0), applied last
    --dry-run            print the selection (with index.csv obligations) and exit

The Rust bench's selector and this one sort over identical stems (`.gc` vs `.json`,
same prefix, same canonical order). To keep ranges stable across machines and reruns,
build `ZKMC_BENCH_FILES` from this script's manifest `pass` rows (sorted) rather than
from `find` — that way failed exports never produce holes that shift indices, and
the bench never sees a missing JSON.

Usage examples:
    uv run python benchmarks/mcc/export_farkas.py                           # full export
    uv run python benchmarks/mcc/export_farkas.py --range 0..100            # first 100
    uv run python benchmarks/mcc/export_farkas.py --exams LTLCardinality --random 50
    uv run python benchmarks/mcc/export_farkas.py --max-obligations 1000    # calibration set
    uv run python benchmarks/mcc/export_farkas.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path


_DEFAULT_GC_DIR = Path(__file__).parent / "rankings" / "gc"
_DEFAULT_OUT_DIR = (Path(__file__).resolve().parents[2]
                    / "benchmarks" / "corpus" / "mcc2025" / "farkas-json")


# --- selection helpers -------------------------------------------------------


def parse_rust_range(spec: str, total: int) -> tuple[int, int]:
    """Half-open range in Rust syntax: `0..100`, `100..`, `..100`, or `..` (all).

    Hard-errors on malformed specs; clamps to [0, total].
    """
    if ".." not in spec:
        sys.exit(f"bad range '{spec}': expected START..END, START.. or ..END")
    s, e = spec.split("..", 1)
    if s == "" and e == "":
        return 0, total
    if s == "":
        start = 0
    else:
        try:
            start = int(s)
        except ValueError:
            sys.exit(f"bad range '{spec}': START not a number")
    if e == "":
        end = total
    else:
        try:
            end = int(e)
        except ValueError:
            sys.exit(f"bad range '{spec}': END not a number")
    if start > end:
        sys.exit(f"bad range '{spec}': START > END")
    return min(start, total), min(end, total)


def load_index_csv(gc_dir: Path) -> dict[str, dict[str, str]] | None:
    """Find rankings/index.csv by a few conventional relative locations."""
    for cand in [
        gc_dir / "index.csv",
        gc_dir.parent / "index.csv",
        gc_dir.parent / "rankings" / "index.csv",
    ]:
        if cand.exists():
            with open(cand) as fh:
                return {r["id"]: r for r in csv.DictReader(fh)}
    return None


def select_files(paths: list[Path], args, index) -> list[str]:
    files: list[str] = sorted(
        str(f) for p in paths for f in (p.glob("*.gc") if p.is_dir() else [p])
    )
    if args.exams:
        exams = set(args.exams.split(","))
        files = [f for f in files if any(ex in Path(f).stem for ex in exams)]
    if index and (args.min_obligations is not None or args.max_obligations is not None):
        def obls(f: str) -> int | None:
            row = index.get(Path(f).stem)
            return int(row["obligations"]) if row else None
        if args.min_obligations is not None:
            files = [f for f in files if (n := obls(f)) is not None and n >= args.min_obligations]
        if args.max_obligations is not None:
            files = [f for f in files if (n := obls(f)) is not None and n <= args.max_obligations]
    if args.range is not None:
        s, e = parse_rust_range(args.range, len(files))
        files = files[s:e]
    if args.random:
        n = min(args.random, len(files))
        files = sorted(random.Random(0).sample(files, n))
    return files


# --- jobs / cap --------------------------------------------------------------


def _ram_available_bytes() -> int | None:
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024  # kB -> bytes
    except OSError:
        return None


def resolve_jobs(spec: str | int, mem_limit_mb: int) -> tuple[int, int | None]:
    """Return (jobs, ram_cap_bytes). `ram_cap_bytes` is None if unavailable.

    spec='all' -> min(cpu, RAM/mem_limit). Explicit int -> used as-is.
    """
    cpu = os.cpu_count() or 1
    avail = _ram_available_bytes()
    cap_jobs = (avail // (mem_limit_mb << 20)) if avail else None
    if spec == "all":
        return (max(1, min(cpu, cap_jobs)) if cap_jobs is not None else cpu,
                avail)
    n = int(spec)
    return n, avail


def _jobs_arg(s: str) -> str | int:
    if s == "all":
        return "all"
    try:
        n = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError("--jobs expects a positive integer or 'all'") from None
    if n < 1:
        raise argparse.ArgumentTypeError("--jobs must be >= 1 or 'all'")
    return n


# --- worker ------------------------------------------------------------------


def _export(path: str, out_dir: str, mem_limit_mb: int, pretty: bool, q: mp.Queue) -> None:
    """Parse + verify + dump. RLIMIT_AS is set per child for OOM isolation."""
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (mem_limit_mb << 20, resource.RLIM_INFINITY))
    from zkterm_tool.farkas_cli import extract_farkas_obligations_from_result
    from zkterm_tool.parser import parse_with_constants

    row: dict = {"file": path, "status": "error", "obligations": "", "seconds": "", "bytes": ""}
    t0 = time.time()
    try:
        result = parse_with_constants(Path(path).read_text(), resolve_ltl=True)
        obls = extract_farkas_obligations_from_result(result)
        # The bench requires: satisfiable (witness non-null), non-empty A_s/G_p rows.
        bad = [i for i, o in enumerate(obls)
               if not o.get("satisfiable")
               or not o.get("matrices", {}).get("A_s")
               or not o.get("matrices", {}).get("G_p")]
        if not obls:
            row["status"] = "no-obligations"
        elif bad:
            row["status"] = f"bad-obligation: {bad[:3]}"
        else:
            out = Path(out_dir) / (Path(path).stem + ".json")
            payload = {
                "obligations": obls,
                "count": len(obls),
                "constants": dict(getattr(result, "constants", None) or {}),
            }
            with open(out, "w") as fh:
                if pretty:
                    # Default Python separators (', ', ': ') — matches zkfarkas default
                    # and the byte-format of the existing folding/input/*.json files.
                    json.dump(payload, fh)
                else:
                    # Compact: ~15% smaller on integer-heavy JSON.
                    json.dump(payload, fh, separators=(",", ":"))
            row.update(status="pass", obligations=len(obls), bytes=out.stat().st_size)
    except MemoryError:
        row["status"] = "memory"
    except Exception as e:  # noqa: BLE001
        tail = str(e).splitlines()[0][:120] if str(e) else ""
        row["status"] = f"error: {type(e).__name__}: {tail}"
    row["seconds"] = round(time.time() - t0, 2)
    resource.setrlimit(resource.RLIMIT_AS, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    q.put(row)


# --- main loop ---------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Export verified .gc rankings as Farkas-dual JSON for the folding bench."
    )
    ap.add_argument("paths", nargs="*", type=Path,
                    help=f"Files or directories (default: {_DEFAULT_GC_DIR})")
    ap.add_argument("--range", default=None,
                    help="Half-open slice in Rust syntax, e.g. 0..100, 100.., ..100")
    ap.add_argument("--exams", default=None,
                    help="Comma-separated exams to keep (e.g. LTLCardinality,LTLFireability)")
    ap.add_argument("--min-obligations", type=int, default=None)
    ap.add_argument("--max-obligations", type=int, default=None)
    ap.add_argument("--random", type=int, default=None,
                    help="Deterministic random sample (seed 0), applied after other filters")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the selection (with obligations from index.csv) and exit")
    ap.add_argument("--jobs", type=_jobs_arg, default=2,
                    help="Worker count, or 'all' for min(cpu, RAM/mem-limit) (default: 2)")
    ap.add_argument("--timeout", type=float, default=1200.0, help="Per-file wall timeout (s)")
    ap.add_argument("--mem-limit-mb", type=int, default=3000,
                    help="RLIMIT_AS per worker (MB); default 3000")
    ap.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT_DIR,
                    help="Directory for emitted .json files")
    ap.add_argument("--out", default=None,
                    help="Manifest CSV (also writes <out>.jsonl journal for resume)")
    ap.add_argument("--pretty", action="store_true",
                    help="Use zkfarkas-compatible spaced JSON (default: compact)")
    ap.add_argument("--retry-status", default="timeout,error,crashed,memory",
                    help="Comma-separated status prefixes to re-run on resume "
                         "(default: timeout,error,crashed,memory)")
    ap.add_argument("--force", action="store_true",
                    help="Ignore the journal and re-export everything")
    args = ap.parse_args()

    paths = args.paths if args.paths else [_DEFAULT_GC_DIR]
    index = load_index_csv(paths[0] if len(paths) == 1 else _DEFAULT_GC_DIR)
    if (args.min_obligations is not None or args.max_obligations is not None) and index is None:
        sys.exit("need rankings/index.csv for --min/--max-obligations (not found)")

    files = select_files(paths, args, index)
    print(f"{len(files)} files selected", flush=True)

    if args.dry_run:
        for f in files:
            n = ""
            if index:
                row = index.get(Path(f).stem)
                if row:
                    n = row["obligations"]
            print(f"  {Path(f).stem:60}  obligations={n}")
        return 0

    os.makedirs(args.out_dir, exist_ok=True)

    # Resume from journal
    journal = Path(args.out + ".jsonl") if args.out else None
    done: dict[str, dict] = {}
    last_status: dict[str, str] = {}
    if journal and journal.exists():
        for line in journal.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                last_status[r["file"]] = r.get("status", "")
                if r.get("status") == "pass":
                    done[r["file"]] = r

    if args.force and journal and journal.exists():
        journal.unlink()
        done = {}
        last_status = {}

    # Prefix matching: journaled statuses carry detail suffixes ("error: ValueError: ...",
    # "crashed (-9)"), so exact membership would never match a --retry-status entry.
    retry_statuses = tuple(args.retry_status.split(","))

    def should_retry(status: str) -> bool:
        return any(status.startswith(s) for s in retry_statuses)

    # Files to (re)run: not already `pass`, and (force) or (last status should retry, or no entry)
    todo = [f for f in files
            if f not in done
            and (args.force or should_retry(last_status.get(f, "")) or f not in last_status)]

    jobs, ram_avail = resolve_jobs(args.jobs, args.mem_limit_mb)
    if ram_avail is not None:
        cap_str = f"{ram_avail // (1024 ** 3)}GB"
        cap_jobs = ram_avail // (args.mem_limit_mb << 20)
        print(f"jobs={jobs} (RAM cap: {args.mem_limit_mb}MB × jobs ≤ {cap_str} available; "
              f"max {cap_jobs} workers by RAM)", flush=True)
        if cap_jobs < jobs:
            print(f"  warning: --jobs {jobs} exceeds RAM cap of {cap_jobs}", flush=True)
    else:
        print(f"jobs={jobs} (RAM cap unavailable; no safety check)", flush=True)
    print(f"  out-dir: {args.out_dir}", flush=True)
    print(f"  {len(done)} already done, {len(todo)} to go", flush=True)
    if not todo:
        print("nothing to do", flush=True)
        return 0

    # Prime the parser import in the parent so forked workers don't repeat the heavy import
    import zkterm_tool.parser  # noqa: F401

    rows = list(done.values())
    running: dict[str, tuple[mp.Process, float]] = {}
    todo = list(todo)
    q: mp.Queue = mp.Queue()
    shown = len(rows)
    total_target = len(done) + len(todo)

    try:
        while todo or running:
            while todo and len(running) < jobs:
                f = todo.pop()
                p = mp.Process(target=_export,
                               args=(f, str(args.out_dir), args.mem_limit_mb, args.pretty, q),
                               daemon=True)
                p.start()
                running[f] = (p, time.time())
            time.sleep(0.05)
            while not q.empty():
                row = q.get()
                rows.append(row)
                running.pop(row["file"])[0].join()
                if journal:
                    with open(journal, "a") as fh:
                        fh.write(json.dumps(row) + "\n")
            for f, (p, t0) in list(running.items()):
                if time.time() - t0 > args.timeout:
                    p.kill()
                    p.join()
                    running.pop(f)
                    r = {"file": f, "status": "timeout",
                         "obligations": "", "seconds": args.timeout, "bytes": ""}
                    rows.append(r)
                    if journal:
                        with open(journal, "a") as fh:
                            fh.write(json.dumps(r) + "\n")
                elif not p.is_alive() and p.exitcode != 0:
                    p.join()
                    running.pop(f)
                    r = {"file": f, "status": f"crashed ({p.exitcode})",
                         "obligations": "", "seconds": "", "bytes": ""}
                    rows.append(r)
                    if journal:
                        with open(journal, "a") as fh:
                            fh.write(json.dumps(r) + "\n")
            if len(rows) >= shown + 100:
                shown = len(rows)
                print(f"  ... {len(rows)}/{total_target}", flush=True)
    except KeyboardInterrupt:
        print("\nInterrupted; killing running workers...")
        for f, (p, _) in running.items():
            p.kill()
        print(f"  {len(rows) - len(done)} completed before interrupt")

    if args.out:
        with open(args.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["file", "status", "obligations", "seconds", "bytes"])
            w.writeheader()
            w.writerows(sorted(rows, key=lambda r: r["file"]))
        print(f"wrote {args.out}")

    by_status = Counter(r["status"] for r in rows)
    print(f"{len(rows)} files:", dict(by_status))
    for r in sorted(rows, key=lambda r: r["file"]):
        if r["status"] != "pass":
            print(f"  {r['status']:40} {Path(r['file']).stem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
