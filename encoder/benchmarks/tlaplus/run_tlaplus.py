"""Run zkmc on the finite TLC models of the TLA+ Examples repository.

Two phases, each journaled (rerunning resumes):

1. `translate`: for every selected model (exhaustive TLC search that succeeds, at most
   `--max-states` distinct states), TLC dumps the state graph (without symmetry or views),
   `tla2gc` translates the model, and the `.gc` program's reachable states and transitions
   (other than stuttering) are compared with TLC's (`mismatch` if they differ). Every invariant and temporal property of the model then
   becomes one instance `.gc` file in `<work>/gc/`.
2. `prove`: for every instance, Spot derives the Büchi automaton, and the MCC pipeline
   (`benchmarks/mcc/run_spike.prove`: Houdini invariants, ranking synthesis, minimization,
   verification) proves it; verified files (with rankings) go to `<work>/rankings/`.

Usage:
  uv run --group tlaplus python benchmarks/tlaplus/run_tlaplus.py translate \\
      --examples benchmarks/corpus/tlaplus-examples --work benchmarks/corpus/tlaplus
  uv run --group tlaplus python benchmarks/tlaplus/run_tlaplus.py prove \\
      --work benchmarks/corpus/tlaplus --houdini --samples 50000 --minimize --synth-budget 300
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from multiprocessing import Process, Queue
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "mcc"))


# ----------------------------------------------------------------------------------------------
# Model selection
# ----------------------------------------------------------------------------------------------

def discover(examples: Path, max_states: int, results=("success",), specs: set | None = None) -> list[dict]:
    out = []
    for mf in sorted((examples / "specifications").glob("*/manifest.json")):
        spec = mf.parent.name
        if specs and spec not in specs:
            continue
        m = json.loads(mf.read_text())
        for mod in m["modules"]:
            for model in mod["models"]:
                if model["mode"] != "exhaustive search" or model["result"] not in results:
                    continue
                n = model.get("distinctStates")
                # Failing models stop early, so their state count is unknown: TLC's timeout decides.
                if (n is None and model["result"] == "success") or (n is not None and n > max_states):
                    continue
                cfg = examples / model["path"]
                module = examples / mod["path"]
                rel = cfg.relative_to(examples / "specifications").with_suffix("")
                out.append(dict(id="__".join(rel.parts), spec=spec, spec_dir=str(module.parent),
                                module=module.stem, cfg=str(cfg), result=model["result"],
                                manifest_states=n))
    return out


# ----------------------------------------------------------------------------------------------
# Phase 1: translate
# ----------------------------------------------------------------------------------------------

def reachable_states(gc_text: str, edges: set | None = None):
    """Program states reachable in the `.gc` (ignoring any property), as (variables, set); the
    transitions between distinct states are added to `edges` if given."""
    from explicit_check import _product
    from zkterm_tool.parser import parse_with_constants

    res = parse_with_constants(gc_text, resolve_ltl=False)
    variables, x0s, succ, _ = _product(res)
    seen = set(x0s)
    stack = list(x0s)
    while stack:
        x = stack.pop()
        for y in succ(x):
            if edges is not None and y != x:
                edges.add((x, y))
            if y not in seen:
                seen.add(y)
                stack.append(y)
    return variables, seen


def translate_model(task: dict) -> dict:
    import tla2gc
    import tlc
    from tla_front import parse_cfg
    work = Path(task["work"])
    row = {"id": task["id"], "model": task["id"], "spec": task["spec"], "manifest_states": task["manifest_states"]}
    t0 = time.time()
    r = tlc.explore(Path(task["spec_dir"]), task["module"], Path(task["cfg"]), work / "tlc" / task["id"],
                    timeout=task["tlc_timeout"])
    row["tlc_s"] = round(time.time() - t0, 2)
    if not r.ok:
        row["status"] = "tlc-error"
        row["detail"] = next((l for l in r.log.splitlines() if "rror" in l), r.log[-80:])[:100]
        return row
    row["tlc_states"] = r.distinct
    row["tlc_initial"] = r.initial
    states, tlc_edges = tlc.read_graph(r.dump)
    cfg = parse_cfg(Path(task["cfg"]).read_text())
    t0 = time.time()
    try:
        tr = tla2gc.build(Path(task["spec_dir"]), task["module"], cfg, states, task["id"])
    except tla2gc.TranslationError as e:
        row["status"] = "no-variables" if str(e) == "no variables" else "unsupported"
        row["detail"] = str(e).splitlines()[0][:120]
        return row
    row["translate_s"] = round(time.time() - t0, 2)
    row["variables"] = len(tr.variables)
    row["slots"] = len(tr.slots)
    row["commands"] = len(tr.commands)
    row["init_states"] = tr.init_states
    row["fairness"] = len(tr.fairness)
    row["boot"] = int(bool(tr.boot_slot))
    plain = tr.render(None)
    t0 = time.time()
    gc_edges = set()
    variables, reach = reachable_states(plain, gc_edges)
    if tr.boot_slot:  # compare the states after the boot step, without the boot slot
        b = variables.index(tr.boot_slot)
        reach = {x[:b] + x[b + 1:] for x in reach if x[b] == 1}
        gc_edges = {(x[:b] + x[b + 1:], y[:b] + y[b + 1:]) for x, y in gc_edges if x[b] == 1}
        variables = variables[:b] + variables[b + 1:]
    enc = []
    for st in states:
        e = {}
        for v in tr.variables:
            e.update(tr.layouts[v].encode(st[v]))
        enc.append(tuple(e[s] for s in variables))
    tlc_edges = {(enc[i], enc[j]) for i, j in tlc_edges if i != j}
    row["check_s"] = round(time.time() - t0, 2)
    row["gc_states"] = len(reach)
    row["tlc_edges"] = len(tlc_edges)
    if reach != set(enc):
        enc = set(enc)
        row["status"] = "mismatch"
        row["detail"] = f"gc reaches {len(reach)} states ({len(reach - enc)} extra), TLC {len(enc)} ({len(enc - reach)} missed)"
        return row
    if gc_edges != tlc_edges:
        row["status"] = "mismatch"
        row["detail"] = (f"gc has {len(gc_edges)} transitions ({len(gc_edges - tlc_edges)} extra), "
                         f"TLC {len(tlc_edges)} ({len(tlc_edges - gc_edges)} missed)")
        return row
    row["status"] = "translated"
    gc_dir = work / "gc"
    gc_dir.mkdir(parents=True, exist_ok=True)
    (gc_dir / f"{task['id']}.gc").write_text(plain)
    instances = []
    import signal

    def alarm(*_):
        raise TimeoutError
    signal.signal(signal.SIGALRM, alarm)
    for kind, names in (("inv", cfg.invariants), ("prop", cfg.properties)):
        for name in names:
            base = {"model": task["id"], "kind": kind, "property": name}
            signal.alarm(int(task["prop_timeout"]))  # one slow or huge property must not lose the model
            try:
                props = [tla2gc.invariant_property(tr, name)] if kind == "inv" else tla2gc.temporal_property(tr, name)
            except tla2gc.Tautology:
                instances.append({**base, "instance": f"{task['id']}__{name}", "status": "tautology"})
                continue
            except tla2gc.TranslationError as e:
                instances.append({**base, "instance": f"{task['id']}__{name}", "status": "unsupported",
                                  "detail": str(e).splitlines()[0][:120]})
                continue
            except (TimeoutError, MemoryError) as e:
                instances.append({**base, "instance": f"{task['id']}__{name}",
                                  "status": "timeout" if isinstance(e, TimeoutError) else "memory"})
                continue
            finally:
                signal.alarm(0)
            for prop in props:
                inst = {**base, "instance": f"{task['id']}__{_file_safe(prop.name)}", "part": prop.name,
                        "status": "ok", "preds": len(prop.preds), "aut_trans": len(prop.transitions),
                        "fairness": len(tr.fairness) if kind == "prop" else 0}
                (gc_dir / f"{inst['instance']}.gc").write_text(tr.render(prop))
                instances.append(inst)
    row["instances"] = instances
    return row


def _file_safe(name: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_.=,\[\]-]", "_", name).replace("[", "(").replace("]", ")")


# ----------------------------------------------------------------------------------------------
# Phase 2: prove
# ----------------------------------------------------------------------------------------------

def prove_instance(task: dict) -> dict:
    from run_spike import _render_rankings, prove
    from zkterm_tool.parser import parse_with_constants

    row = {"instance": task["instance"]}

    def phase(name):
        if task.get("phase_file"):
            Path(task["phase_file"]).write_text(name)

    gc = Path(task["gc"]).read_text()
    task["groups"] = slot_groups(gc)
    phase("spot")
    t0 = time.time()
    try:
        result = parse_with_constants(gc, resolve_ltl=True)
    except ValueError as e:
        msg = str(e)
        row["status"] = "tautology" if "is unsatisfiable" in msg else "spot-error"
        row["detail"] = " ".join(msg.splitlines()[1:])[-120:]
        return row
    row["ltl_s"] = round(time.time() - t0, 2)
    row["aut_states"] = len({t.from_state for t in result.automaton_transitions}
                            | {t.to_state for t in result.automaton_transitions})
    row["aut_trans"] = len(result.automaton_transitions)
    row["commands"] = len(result.commands)
    if task["explicit"]:
        from explicit_check import TooLarge, check
        phase("explicit")
        t0 = time.time()
        try:
            ex = check(result, budget=task["explicit_budget"])
            row["explicit"] = "TRUE" if ex.holds else "FALSE"
            row["product_states"] = ex.product_states
        except TooLarge:
            row["explicit"] = "too-large"
        row["explicit_s"] = round(time.time() - t0, 2)
        if row["explicit"] == "FALSE" and not task.get("synth_all"):
            row["status"] = "explicit-false"  # the property fails (for a TLC-verified model: a bug)
            return row
    if not task["synth"]:
        row["status"] = "translated"
        return row
    prove(result, task, row, phase)
    if row.get("status") == "pass" and task["emit"]:
        out = Path(task["emit"]) / f"{task['instance']}.gc"
        out.write_text(gc + "\n" + _render_rankings(result))
    return row


def slot_groups(gc: str) -> list:
    """The slots of each TLA+ variable, from the header of a translated `.gc` file (for the
    group-sum guesses of Houdini's relational candidates)."""
    import re
    groups = []
    for m in re.finditer(r"^//   [A-Za-z_][A-Za-z0-9_]*: ((?:[A-Za-z_][A-Za-z0-9_]* ?)+)$", gc, re.M):
        slots = m.group(1).split()
        if len(slots) >= 2:
            groups.append(slots)
    return groups


# ----------------------------------------------------------------------------------------------
# Worker pool (one process per task, wall-clock timeout, address-space cap)
# ----------------------------------------------------------------------------------------------

def _worker(fn_name, task, q):
    import resource
    if task.get("mem_limit_mb"):
        limit = task["mem_limit_mb"] * 1024 * 1024
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))

    def lift():
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS, (hard, hard))
    fn = globals()[fn_name]
    try:
        row = fn(task)
        lift()
        q.put(row)
    except MemoryError:
        lift()
        q.put({task["key_field"]: task[task["key_field"]], "status": "memory",
               "detail": f">{task['mem_limit_mb']} MB"})
    except Exception as e:  # pragma: no cover - defensive
        lift()
        q.put({task["key_field"]: task[task["key_field"]],
               "status": "memory" if "out of memory" in str(e) else "error",
               "detail": f"{type(e).__name__}: {str(e).splitlines()[0][:100] if str(e) else ''}"})


def run_pool(fn_name: str, tasks: list, key_field: str, jobs: int, timeout: float, journal: Path,
             retry: set) -> list:
    import tempfile
    rows = []
    done = {}
    if journal.exists():
        for line in journal.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[r[key_field]] = r
    for k in [k for k, r in done.items() if r.get("status") in retry]:
        del done[k]
    rows = list(done.values())
    tasks = [t for t in tasks if t[key_field] not in done]
    print(f"{len(rows)} done, {len(tasks)} to go", flush=True)
    phase_dir = tempfile.mkdtemp(prefix="tlaplus_phase_")
    active, i, t_start = [], 0, time.time()
    while i < len(tasks) or active:
        while len(active) < jobs and i < len(tasks):
            q: Queue = Queue()
            tasks[i]["phase_file"] = os.path.join(phase_dir, f"{i}.phase")
            tasks[i]["key_field"] = key_field
            p = Process(target=_worker, args=(fn_name, tasks[i], q), daemon=True)
            p.start()
            active.append((p, q, tasks[i], time.time()))
            i += 1
        time.sleep(0.05)
        still = []
        for p, q, task, t0 in active:
            row = None
            if not q.empty() or not p.is_alive():
                try:
                    row = q.get(timeout=2.0)
                except Exception:
                    row = {key_field: task[key_field], "status": "error", "detail": f"no result (exit {p.exitcode})"}
                p.join(timeout=5)
            elif time.time() - t0 > timeout:
                p.terminate()
                p.join()
                try:
                    where = Path(task["phase_file"]).read_text()
                except OSError:
                    where = "?"
                row = {key_field: task[key_field], "status": "timeout", "detail": f"{where} >{timeout:.0f}s wall"}
            if row is None:
                still.append((p, q, task, t0))
                continue
            rows.append(row)
            with open(journal, "a") as fh:
                fh.write(json.dumps(row) + "\n")
            if len(rows) % 10 == 0:
                print(f"  ... {len(rows)} ({time.time() - t_start:.0f}s)", flush=True)
        active = still
    return rows


def write_csv(path: Path, rows: list, keys: list):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


MODEL_KEYS = ["model", "spec", "status", "detail", "manifest_states", "tlc_states", "gc_states", "tlc_edges", "tlc_initial",
              "init_states", "boot", "variables", "slots", "commands", "fairness", "tlc_s", "translate_s", "check_s"]
INSTANCE_KEYS = ["instance", "model", "kind", "property", "part", "status", "detail", "preds", "aut_trans",
                 "fairness"]
PROVE_KEYS = ["instance", "kind", "status", "detail", "commands", "aut_states", "aut_trans", "explicit",
              "product_states", "inv_atoms", "inv_atoms_min", "cases", "inf_cases", "obligations", "method",
              "ltl_s", "explicit_s", "houdini_s", "synth_s", "minimize_s", "cegis_s", "verify_s", "minimize_detail"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="TLA+ Examples benchmark for zkmc.")
    ap.add_argument("phase", choices=["translate", "prove"])
    ap.add_argument("--examples", type=Path, default=Path("benchmarks/corpus/tlaplus-examples"))
    ap.add_argument("--work", type=Path, default=Path("benchmarks/corpus/tlaplus"))
    ap.add_argument("--max-states", type=int, default=200_000)
    ap.add_argument("--results", default="success",
                    help="TLC results of the models to select (comma-separated manifest values, e.g. "
                         "'safety failure,liveness failure' for a soundness check: some property of each "
                         "such model must fail).")
    ap.add_argument("--specs", default=None, help="Comma-separated spec directories.")
    ap.add_argument("--instances", default=None, help="Comma-separated instance names (prove).")
    ap.add_argument("--kinds", default="inv,prop", help="Instance kinds to prove.")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--tlc-timeout", type=float, default=900.0)
    ap.add_argument("--prop-timeout", type=float, default=300.0, help="Per property, when translating.")
    ap.add_argument("--mem-limit-mb", type=int, default=2500)
    ap.add_argument("--retry-status", default="error")
    ap.add_argument("--explicit", action="store_true", help="Explicit product check before proving.")
    ap.add_argument("--explicit-budget", type=int, default=2_000_000)
    ap.add_argument("--no-synth", action="store_true")
    ap.add_argument("--synth-all", action="store_true",
                    help="Also try to prove instances the explicit check refutes (soundness check: these "
                         "must never pass).")
    ap.add_argument("--synth-budget", type=float, default=120.0)
    ap.add_argument("--max-mode-vars", type=int, default=1)
    ap.add_argument("--houdini", action="store_true")
    ap.add_argument("--relational", action="store_true",
                    help="With --houdini and --samples: also guess affine-hull and octagon invariants.")
    ap.add_argument("--samples", type=int, default=0)
    ap.add_argument("--minimize", action="store_true")
    ap.add_argument("--minimize-budget", type=float, default=60.0)
    ap.add_argument("--rational", action="store_true")
    ap.add_argument("--cegis-fallback", action="store_true")
    args = ap.parse_args(argv)
    args.work.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LTL2TGBA_TIMEOUT", "180")  # per Spot translation (benchmarks/mcc/spot)
    os.environ.setdefault("LTL2TGBA_CACHE", str((args.work / "spot-cache").resolve()))
    from zkterm_tool.ltl import find_ltl2tgba
    find_ltl2tgba()  # fail now rather than per model (set ZKTERM_LTL2TGBA, see README)
    retry = set(args.retry_status.split(","))

    if args.phase == "translate":
        specs = set(args.specs.split(",")) if args.specs else None
        models = discover(args.examples, args.max_states, results=tuple(args.results.split(",")), specs=specs)
        print(f"{len(models)} models selected", flush=True)
        tasks = [dict(m, work=str(args.work), tlc_timeout=args.tlc_timeout, mem_limit_mb=args.mem_limit_mb,
                      prop_timeout=args.prop_timeout) for m in models]
        rows = run_pool("translate_model", tasks, "id", args.jobs, args.timeout + args.tlc_timeout,
                        args.work / "translate.jsonl", retry)
        for r in rows:
            r.setdefault("model", r.get("id"))
        rows.sort(key=lambda r: r["model"])
        write_csv(args.work / "models.csv", rows, MODEL_KEYS)
        instances = [i for r in rows for i in r.get("instances", [])]
        write_csv(args.work / "instances.csv", instances, INSTANCE_KEYS)
        from collections import Counter
        print(f"models: {dict(Counter(r['status'] for r in rows))}")
        print(f"instances: {dict(Counter((i['kind'], i['status']) for i in instances))}")
        return 0

    with open(args.work / "instances.csv") as fh:
        instances = [r for r in csv.DictReader(fh) if r["status"] == "ok" and r["kind"] in args.kinds.split(",")]
    if args.instances:
        wanted = set(args.instances.split(","))
        instances = [r for r in instances if r["instance"] in wanted]
    emit = args.work / "rankings"
    emit.mkdir(parents=True, exist_ok=True)
    tasks = []
    for r in instances:
        tasks.append(dict(instance=r["instance"], kind=r["kind"], gc=str(args.work / "gc" / f"{r['instance']}.gc"),
                          explicit=args.explicit, explicit_budget=args.explicit_budget, synth=not args.no_synth,
                          synth_all=args.synth_all,
                          synth_budget=args.synth_budget, max_mode_vars=args.max_mode_vars, houdini=args.houdini,
                          samples=args.samples, relational=args.relational, minimize=args.minimize,
                          minimize_budget=args.minimize_budget,
                          rational=args.rational, cegis=args.cegis_fallback, emit=str(emit),
                          mem_limit_mb=args.mem_limit_mb))
    import random
    random.Random(0).shuffle(tasks)
    rows = run_pool("prove_instance", tasks, "instance", args.jobs, args.timeout, args.work / "prove.jsonl", retry)
    kinds = {r["instance"]: r["kind"] for r in instances}
    for r in rows:
        r["kind"] = kinds.get(r["instance"], r.get("kind"))
    rows.sort(key=lambda r: r["instance"])
    write_csv(args.work / "prove.csv", rows, PROVE_KEYS)
    from collections import Counter
    print(f"prove: {dict(Counter((r.get('kind'), r['status']) for r in rows))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
