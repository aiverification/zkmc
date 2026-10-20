"""Spike: run zkmc on MCC P/T LTL formulas and compare with the MCC oracle verdicts.

For every selected (instance, examination, formula): translate to `.gc` (mcc2gc), derive the
Büchi automaton via Spot, and

- `--explicit`: explore the product explicitly and compare the verdict with the oracle
  (checks the translation end to end; also reports the size of the exact ranking);
- `--synth`: for oracle-TRUE formulas, synthesize a ranking, validate and verify it.

Each formula runs in a child process with a wall-clock timeout. Results go to a CSV.

Usage:
  uv run python benchmarks/mcc/run_spike.py --corpus benchmarks/corpus/mcc2025 \
      --max-size 100 --explicit --synth --out benchmarks/mcc_spike.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from multiprocessing import Process, Queue
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

EXAMS = {"LTLCardinality": "LTLC", "LTLFireability": "LTLF"}


def oracle_verdicts(oracle: Path, instance: str, exam: str) -> dict[str, str]:
    path = oracle / f"{instance}-{EXAMS[exam]}.out"
    if not path.exists():
        return {}
    return dict(re.findall(r"^FORMULA (\S+) (\S+)", path.read_text(), re.M))


def _oracle_scalar(oracle: Path, instance: str, suffix: str, pattern: str) -> str | None:
    path = oracle / f"{instance}-{suffix}.out"
    if not path.exists():
        return None
    m = re.search(pattern, path.read_text(), re.M)
    return m.group(1) if m else None


def _run_one(task: dict) -> dict:
    """Runs in a child process."""
    from mcc2gc import TooManyCubes, deadlock_cubes, net_to_gc, parse_pnml, parse_properties, read_instance
    from zkterm_tool.parser import parse_with_constants

    row = {k: task[k] for k in ("instance", "exam", "index", "oracle")}

    def phase(name):
        if task.get("phase_file"):
            Path(task["phase_file"]).write_text(name)

    phase("translate")
    files = read_instance(Path(task["tgz"]))
    net = parse_pnml(files["model.pnml"])
    prop = parse_properties(files[f"{task['exam']}.xml"])[task["index"]]
    row["places"], row["transitions"] = len(net.places), len(net.transitions)
    stutter = None
    if task.get("deadlock"):
        phase("deadlock-cubes")
        try:
            stutter = deadlock_cubes(net)
        except TooManyCubes as e:
            row["status"] = "unsupported"
            row["detail"] = f"deadlock cubes: {e}"[:80]
            return row
        row["stutter_cubes"] = len(stutter)
    try:
        gc = net_to_gc(net, prop, task["bound"], task["instance"], stutter=stutter)
    except ValueError as e:
        row["status"] = "unsupported"
        row["detail"] = str(e)[:80]
        return row
    t0 = time.time()
    phase("spot")
    try:
        result = parse_with_constants(gc, resolve_ltl=True)
    except ValueError as e:
        msg = str(e)
        if "is unsatisfiable" in msg:
            row["status"] = "tautology"  # the property holds for every program: nothing to prove
        else:
            row["status"] = "spot-error"
            row["detail"] = " ".join(msg.splitlines()[1:])[-120:]
        return row
    row["ltl_s"] = round(time.time() - t0, 2)
    row["aut_states"] = len({t.from_state for t in result.automaton_transitions}
                            | {t.to_state for t in result.automaton_transitions})
    row["aut_trans"] = len(result.automaton_transitions)
    row["aps"] = len(result.aps)

    if task["explicit"]:
        from explicit_check import TooLarge, check
        phase("explicit")
        t0 = time.time()
        try:
            ex = check(result, budget=task["explicit_budget"])
            row["explicit"] = "TRUE" if ex.holds else "FALSE"
            row["product_states"] = ex.product_states
            row["markings"] = ex.markings
            row["max_rank"] = ex.max_rank
            row["distinct_ranks"] = ex.distinct_ranks
        except TooLarge:
            row["explicit"] = "too-large"
        row["explicit_s"] = round(time.time() - t0, 2)

    if task["synth"] and (task["oracle"] == "TRUE" or task["synth_all"]):
        prove(result, task, row, phase)
        if row.get("status") == "pass" and task["emit"]:
            out = Path(task["emit"]) / f"{prop.id}.gc"
            out.write_text(gc + "\n" + _render_rankings(result))
    return row


def prove(result, task: dict, row: dict, phase) -> dict:
    """Synthesize a ranking for `result` (optionally with Houdini invariants, minimization and
    the CEGIS fallback, as configured in `task`), validate and verify it. Sets `row["status"]`
    (`pass` iff verified) and timing/size columns; leaves the ranking in `result`."""
    from zkterm_tool import validate_ranking_function, verify_termination
    from zkterm_tool.ranking_encoder import encode_ranking_functions
    from zkterm_tool.synth import SynthesisError, synthesize_into
    invariants = None
    if task["houdini"]:
        from invariants import houdini
        phase("houdini")
        t0 = time.time()
        try:
            invariants = houdini(result, time_budget=task["synth_budget"],
                                 sample_budget=task["samples"], relational=task.get("relational", False),
                                 groups=task.get("groups"))
        except TimeoutError:
            row["status"] = "timeout"
            row["detail"] = "houdini"
            return row
        row["houdini_s"] = round(time.time() - t0, 2)
        row["inv_atoms"] = max(len(cs) for cs in invariants.values())
    phase("synth")
    t0 = time.time()
    try:
        synthesize_into(result, max_mode_vars=task["max_mode_vars"],
                        time_budget=task["synth_budget"], invariants=invariants,
                        rational=task.get("rational", False))
    except SynthesisError as e:
        row["synth_s"] = round(time.time() - t0, 2)
        if invariants is not None and task.get("cegis"):
            # Fallback: guess-and-check a linear piece per automaton state from samples and
            # counterexamples (scales to large automata where the Farkas LP does not).
            from cegis import CegisFailure, cegis_rankings
            phase("cegis")
            t0 = time.time()
            try:
                result.ranking_functions = cegis_rankings(result, invariants,
                                                          time_budget=task["synth_budget"])
                row["cegis_s"] = round(time.time() - t0, 2)
                row["method"] = "cegis"
            except CegisFailure as ce:
                row["status"] = "timeout" if "budget" in str(ce) else "no-ranking"
                row["detail"] = f"cegis: {ce}"[:80]
                return row
        else:
            row["status"] = "timeout" if "budget" in str(e) else "no-ranking"
            return row
    except ValueError as e:
        row["status"] = "error"
        row["detail"] = str(e).splitlines()[0][:80]
        return row
    row["synth_s"] = round(time.time() - t0, 2)
    if invariants is not None and task.get("minimize"):
        # Keep only the invariant atoms the ranking's proof uses (smaller inf ladders, so far
        # fewer obligations); fall back to the full invariants if anything goes wrong.
        from invariants import needed_atoms
        from zkterm_tool.synth import synthesize_rankings
        phase("minimize")
        t0 = time.time()
        try:
            small = needed_atoms(result, invariants, result.ranking_functions,
                                 time_budget=task.get("minimize_budget", 60))
            import dataclasses
            rankings = synthesize_rankings(dataclasses.replace(result, ranking_functions={}),
                                           max_mode_vars=task["max_mode_vars"],
                                           time_budget=task.get("minimize_budget", 60),
                                           invariants=small,
                                           rational=task.get("rational", False))
            result.ranking_functions = rankings
            row["inv_atoms_min"] = max(len(cs) for cs in small.values())
        except (TimeoutError, SynthesisError, RuntimeError) as e:
            row["minimize_detail"] = f"{type(e).__name__}: {str(e)[:60]}"
        row["minimize_s"] = round(time.time() - t0, 2)
    for state, enc in encode_ranking_functions(result.ranking_functions).items():
        ok, errors = validate_ranking_function(enc.finite_cases, enc.infinity_cases, enc.variables)
        if not ok:
            row["status"] = "verify-fail"
            row["detail"] = f"invalid ranking ({state}): {errors[0][:50]}"
            return row
    phase("verify")
    t0 = time.time()
    v = verify_termination(result)
    row["verify_s"] = round(time.time() - t0, 2)
    row["cases"] = sum(1 for rf in result.ranking_functions.values()
                       for c in rf.cases if not c.is_infinity)
    row["inf_cases"] = sum(1 for rf in result.ranking_functions.values()
                           for c in rf.cases if c.is_infinity)
    row["obligations"] = len(v.obligations)
    row["status"] = "pass" if v.passed else "verify-fail"
    return row


def _render_rankings(result) -> str:
    return "\n\n".join(str(rf) for rf in result.ranking_functions.values()) + "\n"


def _lift_memory_limit():
    import resource
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    resource.setrlimit(resource.RLIMIT_AS, (hard, hard))


def _worker(task, q):
    if task.get("mem_limit_mb"):
        # Cap the address space so one runaway formula fails alone instead of OOM-killing the host.
        import resource
        limit = task["mem_limit_mb"] * 1024 * 1024
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))  # soft limit only: liftable below
    try:
        row = _run_one(task)
        _lift_memory_limit()
        q.put(row)
    except MemoryError:
        _lift_memory_limit()  # reporting needs a queue feeder thread, i.e. fresh address space
        row = {k: task[k] for k in ("instance", "exam", "index", "oracle")}
        row["status"] = "memory"
        row["detail"] = f">{task['mem_limit_mb']} MB"
        q.put(row)
    except Exception as e:  # pragma: no cover - defensive
        _lift_memory_limit()
        row = {k: task[k] for k in ("instance", "exam", "index", "oracle")}
        row["status"] = "memory" if "out of memory" in str(e) else "error"
        row["detail"] = f"{type(e).__name__}: {str(e).splitlines()[0][:70] if str(e) else ''}"
        q.put(row)


def select_instances(corpus: Path, max_size: int, max_digits: int | None, models: set[str] | None,
                     deadlocks: str = "exclude"):
    """Deadlock-free P/T instances with at most `max_size` places + transitions.

    Net sizes come from `net_sizes.csv` in the corpus (written by net_sizes.py), so that huge
    PNML files never need to be parsed just to be skipped."""
    oracle = corpus / "oracle"
    sizes = {}
    with open(corpus / "net_sizes.csv") as fh:
        for r in csv.DictReader(fh):
            sizes[r["instance"]] = int(r["places"]) + int(r["transitions"])
    picked = []
    for tgz in sorted((corpus / "INPUTS-2025").glob("*-PT-*.tgz")):
        inst = tgz.name[:-4]
        if models and inst.split("-")[0] not in models:
            continue
        if sizes.get(inst, max_size + 1) > max_size:
            continue
        has_deadlock = _oracle_scalar(oracle, inst, "RD", r"^FORMULA \S+ (\S+)")
        if has_deadlock not in ("TRUE", "FALSE"):
            continue
        if deadlocks == "exclude" and has_deadlock == "TRUE":
            continue
        if deadlocks == "only" and has_deadlock == "FALSE":
            continue
        digits = _oracle_scalar(oracle, inst, "SS", r"STATES (\d+)")
        if max_digits is not None and (digits is None or len(digits) > max_digits):
            continue
        bound = _oracle_scalar(oracle, inst, "SS", r"MAX_TOKEN_IN_PLACE (\d+)")
        picked.append((tgz, inst, int(bound) if bound else None, has_deadlock == "TRUE"))
    return picked


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MCC LTL spike for zkmc.")
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--max-size", type=int, default=100, help="Max places + transitions.")
    ap.add_argument("--deadlocks", choices=["exclude", "include", "only"], default="exclude",
                    help="Nets with reachable deadlocks get stutter commands (MCC semantics).")
    ap.add_argument("--max-state-digits", type=int, default=None,
                    help="Only instances whose reachable state count has at most this many digits.")
    ap.add_argument("--models", default=None, help="Comma-separated model names.")
    ap.add_argument("--exams", default="LTLCardinality,LTLFireability")
    ap.add_argument("--formulas", default=None, help="Comma-separated formula indices (default all).")
    ap.add_argument("--only-true", action="store_true", help="Skip formulas the oracle marks FALSE.")
    ap.add_argument("--only-false", action="store_true", help="Skip formulas the oracle does not mark FALSE.")
    ap.add_argument("--limit", type=int, default=None, help="Run a random sample of this many formulas.")
    ap.add_argument("--select-from", default=None, help="Only formulas listed in this results CSV ...")
    ap.add_argument("--select-status", default="timeout", help="... with one of these statuses.")
    ap.add_argument("--no-bound", action="store_true", help="Do not emit place types from MAX_TOKEN_IN_PLACE.")
    ap.add_argument("--explicit", action="store_true")
    ap.add_argument("--explicit-budget", type=int, default=2_000_000)
    ap.add_argument("--synth", action="store_true")
    ap.add_argument("--synth-budget", type=float, default=60.0)
    ap.add_argument("--synth-all", action="store_true",
                    help="Also synthesize on non-TRUE formulas (soundness check: these must never pass).")
    ap.add_argument("--max-mode-vars", type=int, default=1)
    ap.add_argument("--samples", type=int, default=0,
                    help="With --houdini: also guess invariants from this many explored product states.")
    ap.add_argument("--rational", action="store_true",
                    help="Synthesize via the real LP and scale to integers (faster).")
    ap.add_argument("--cegis-fallback", action="store_true",
                    help="With --houdini: if the Farkas LP fails, try CEGIS (benchmarks/mcc/cegis.py).")
    ap.add_argument("--minimize-budget", type=float, default=60.0,
                    help="Time budget for --minimize; on timeout the full invariants are kept.")
    ap.add_argument("--minimize", action="store_true",
                    help="With --houdini: shrink invariants to the atoms the ranking proof needs.")
    ap.add_argument("--houdini", action="store_true",
                    help="Infer per-automaton-state inductive invariants (guess-and-check) for synthesis.")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--mem-limit-mb", type=int, default=1800, help="Address-space limit per formula.")
    ap.add_argument("--retry-status", default="error",
                    help="On resume, re-run journaled formulas with these statuses (comma-separated).")
    ap.add_argument("--emit", default=None, help="Directory for .gc files with verified rankings.")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    models = set(args.models.split(",")) if args.models else None
    instances = select_instances(args.corpus, args.max_size, args.max_state_digits, models,
                                 args.deadlocks)
    print(f"{len(instances)} instances selected", flush=True)
    if args.emit:
        os.makedirs(args.emit, exist_ok=True)

    tasks = []
    for tgz, inst, bound, deadlock in instances:
        for exam in args.exams.split(","):
            verdicts = oracle_verdicts(args.corpus / "oracle", inst, exam)
            idxs = [int(i) for i in args.formulas.split(",")] if args.formulas else range(16)
            for i in idxs:
                verdict = verdicts.get(f"{inst}-{exam}-{i:02d}", "?")
                if args.only_true and verdict != "TRUE":
                    continue
                if args.only_false and verdict != "FALSE":
                    continue
                tasks.append(dict(tgz=str(tgz), instance=inst, exam=exam, index=i, oracle=verdict,
                                  deadlock=deadlock,
                                  bound=None if args.no_bound else bound,
                                  explicit=args.explicit, explicit_budget=args.explicit_budget,
                                  synth=args.synth, synth_budget=args.synth_budget,
                                  synth_all=args.synth_all, mem_limit_mb=args.mem_limit_mb,
                                  max_mode_vars=args.max_mode_vars, houdini=args.houdini,
                                  samples=args.samples, minimize=args.minimize,
                                  minimize_budget=args.minimize_budget, cegis=args.cegis_fallback,
                                  rational=args.rational,
                                  emit=args.emit))
    if args.select_from:
        with open(args.select_from) as fh:
            wanted = {(r["instance"], r["exam"], int(r["index"])) for r in csv.DictReader(fh)
                      if r["status"] in args.select_status.split(",")}
        tasks = [t for t in tasks if (t["instance"], t["exam"], t["index"]) in wanted]

    # Deterministic shuffle so that partial runs are a representative sample.
    import random
    random.Random(0).shuffle(tasks)
    if args.limit:
        tasks = tasks[: args.limit]

    # Results are appended to <out>.jsonl as they complete; a rerun skips formulas already there.
    journal = Path(args.out + ".jsonl") if args.out else None
    rows = []
    if journal and journal.exists():
        import json
        done = {}
        for line in journal.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[(r["instance"], r["exam"], r["index"])] = r
        for key in [k for k, r in done.items() if r.get("status") in args.retry_status.split(",")]:
            del done[key]
        rows = list(done.values())
        tasks = [t for t in tasks if (t["instance"], t["exam"], t["index"]) not in done]
        print(f"resuming: {len(rows)} done, {len(tasks)} to go", flush=True)
    print(f"{len(tasks)} formulas", flush=True)

    import tempfile
    phase_dir = tempfile.mkdtemp(prefix="mcc_spike_phase_")
    active, i = [], 0
    t_start = time.time()
    while i < len(tasks) or active:
        while len(active) < args.jobs and i < len(tasks):
            q: Queue = Queue()
            tasks[i]["phase_file"] = os.path.join(phase_dir, f"{i}.phase")
            p = Process(target=_worker, args=(tasks[i], q), daemon=True)
            p.start()
            active.append((p, q, tasks[i], time.time()))
            i += 1
        time.sleep(0.05)
        still = []
        for p, q, task, t0 in active:
            if not p.is_alive():
                p.join()
                try:
                    row = q.get(timeout=1.0)
                except Exception:
                    row = {k: task[k] for k in ("instance", "exam", "index", "oracle")}
                    row["status"] = "error"
                    row["detail"] = f"no result (exit {p.exitcode})"
            elif time.time() - t0 > args.timeout:
                p.terminate()
                p.join()
                row = {k: task[k] for k in ("instance", "exam", "index", "oracle")}
                row["status"] = "timeout"
                try:
                    where = Path(task["phase_file"]).read_text()
                except OSError:
                    where = "?"
                row["detail"] = f"{where} >{args.timeout:.0f}s wall"
            else:
                still.append((p, q, task, t0))
                continue
            rows.append(row)
            if journal:
                import json
                with open(journal, "a") as fh:
                    fh.write(json.dumps(row) + "\n")
            if len(rows) % 25 == 0:
                print(f"  ... {len(rows)}/{len(tasks)} ({time.time() - t_start:.0f}s)", flush=True)
        active = still

    _summarize(rows)
    if args.out:
        keys = ["instance", "exam", "index", "oracle", "explicit", "status", "detail", "places",
                "transitions", "aps", "aut_states", "aut_trans", "product_states", "markings",
                "max_rank", "distinct_ranks", "inv_atoms", "inv_atoms_min", "cases", "inf_cases",
                "obligations", "method", "stutter_cubes", "ltl_s", "explicit_s", "houdini_s", "synth_s", "minimize_s",
                "cegis_s", "verify_s",
                "minimize_detail"]
        with open(args.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for r in sorted(rows, key=lambda r: (r["instance"], r["exam"], r["index"])):
                w.writerow(r)
        print(f"wrote {args.out}")
    return 0


def _summarize(rows):
    from collections import Counter
    n = len(rows)
    print("\n" + "=" * 60)
    print(f"{n} formulas")
    ex = [r for r in rows if r.get("explicit") in ("TRUE", "FALSE")]
    if ex:
        agree = sum(r["explicit"] == r["oracle"] for r in ex if r["oracle"] in ("TRUE", "FALSE"))
        decided = sum(r["oracle"] in ("TRUE", "FALSE") for r in ex)
        print(f"explicit vs oracle: {agree}/{decided} agree "
              f"({sum(r.get('explicit') == 'too-large' for r in rows)} too large)")
        for r in ex:
            if r["oracle"] in ("TRUE", "FALSE") and r["explicit"] != r["oracle"]:
                print(f"  MISMATCH {r['instance']} {r['exam']} {r['index']}: "
                      f"oracle {r['oracle']}, explicit {r['explicit']}")
    synth = [r for r in rows if r.get("oracle") == "TRUE" and "status" in r]
    if synth:
        c = Counter(r["status"] for r in synth)
        print(f"synthesis on {len(synth)} TRUE formulas: " + ", ".join(f"{k}={v}" for k, v in c.most_common()))
    unsound = [r for r in rows if r.get("oracle") == "FALSE" and r.get("status") == "pass"]
    checked = sum(r.get("oracle") == "FALSE" and "status" in r for r in rows)
    if checked:
        print(f"soundness: {len(unsound)} of {checked} FALSE formulas passed (must be 0)")
        for r in unsound:
            print(f"  UNSOUND {r['instance']} {r['exam']} {r['index']}")
    other = Counter(r["status"] for r in rows if r.get("status") in ("error", "unsupported", "timeout")
                    and r.get("oracle") != "TRUE")
    if other:
        print("other: " + ", ".join(f"{k}={v}" for k, v in other.items()))


if __name__ == "__main__":
    sys.exit(main())
