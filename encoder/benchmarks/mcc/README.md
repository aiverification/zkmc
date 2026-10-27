# MCC LTL benchmark

Runs zkmc on the [Model Checking Contest](https://mcc.lip6.fr/) P/T nets with their
`LTLCardinality` / `LTLFireability` formulas, and compares against the MCC oracle verdicts.

A P/T net is exactly a linear guarded-command program: places are integer variables, a transition
is `[] p >= pre(p,t) && ... -> p = p + post(p,t) - pre(p,t); ...`, and the formula atoms
(`tokens-count` comparisons, `is-fireable`) are linear comparisons. MCC treats deadlocks as
infinite stuttering while `.gc` runs end at a deadlock, so for nets that can deadlock `mcc2gc.py`
adds stutter commands `[] <cube> -> p0 = p0`, where the cubes cover the dead markings (one
disabling literal per transition, generalized under the place invariants; `--deadlocks`).

## Pipeline

1. `mcc2gc.py` — PNML + formula XML → `.gc` with `ap`/`spec:` (Spot derives the Büchi automaton).
2. `invariants.py` — per-automaton-state inductive invariants by guess-and-check (Houdini).
   Guesses: place invariants, state-equation sub-invariants (`y·m <= y·m0` with `yᵀC <= 0`),
   guard/AP atoms and their negations, per-place bounds of explicitly sampled product states,
   `false` for automaton states never sampled, and invariants of the subnet without commands
   that never fired in the samples. Houdini keeps the largest inductive subset, so wrong guesses
   cost nothing but time.
3. `zkterm_tool.synth` with `invariants=`: each state's finite cases are restricted to its
   invariant, everything outside is `inf` (so transition non-infinity holds by construction).
4. `needed_atoms` (optional `--minimize`): unsat cores of the ranking's obligations, closed under
   inductiveness, shrink the invariants to what the proof uses (fewer `inf` cases, so far fewer
   obligations), then re-synthesize.
   If the Farkas template LP is too large (big automata), `--cegis-fallback` guesses one linear
   piece per automaton state from sampled product edges and refines it with counterexamples
   (`cegis.py`); `--rational` solves the template LP over the reals instead of the integers.
5. `zkterm_tool.verify_termination` checks the result; only verified rankings count as `pass`.

`explicit_check.py` independently explores the product and compares with the oracle (translation
check), and computes the exact ranking (max number of fair edges ahead) for small instances.

## Setup

```bash
docker build -t zkmc-spot:local benchmarks/mcc/spot
export ZKTERM_LTL2TGBA=$PWD/benchmarks/mcc/spot/ltl2tgba
export LTL2TGBA_CACHE=$PWD/benchmarks/corpus/mcc2025/spot-cache
# Inputs (1 GB): https://mcc.lip6.fr/2025/archives/INPUTS-2025.tar.gz -> benchmarks/corpus/mcc2025/INPUTS-2025/
# Oracle verdicts (MCC 2025 consensus): https://yanntm.github.io/pnmcc-models-2025/oracle.tar.gz
#   -> benchmarks/corpus/mcc2025/oracle/
uv run python benchmarks/mcc/net_sizes.py benchmarks/corpus/mcc2025/INPUTS-2025 \
    benchmarks/corpus/mcc2025/oracle benchmarks/corpus/mcc2025/net_sizes.csv
```

## Runs

```bash
uv run python benchmarks/mcc/run_spike.py --corpus benchmarks/corpus/mcc2025 --max-size 100 \
    --only-true --no-bound --houdini --samples 50000 --minimize --synth \
    --synth-budget 300 --timeout 480 --jobs 4 --mem-limit-mb 1600 \
    --emit benchmarks/corpus/mcc2025/runs/emit --out benchmarks/corpus/mcc2025/runs/run.csv
```

Results are journaled to `<out>.jsonl` as they complete; rerunning resumes (`--retry-status`
re-runs given statuses). `--explicit` adds the oracle cross-check, `--synth-all --only-false`
is the soundness check (no FALSE formula may pass). `diag.py` traces one formula phase by phase.
Keep `--mem-limit-mb`: without it a single runaway formula can exhaust the host's memory.

`summarize.py` merges runs (later ones override earlier ones per formula) and reports coverage.
`bundle.py` collects the verified `.gc` files of all passing formulas into one directory with an
`index.csv`, writing the Büchi automaton out explicitly so the files no longer need Spot
(`rankings/` was built that way, from the runs above); `reverify.py` re-checks such files from disk.

## Results (MCC 2025, nets with at most 100 places + transitions)

| | TRUE formulas | pass | timeout | other |
|---|---|---|---|---|
| deadlock-free nets | 1269 | 1178 | 70 | 21 |
| nets with deadlocks | 901 | 729 | 92 | 80 |
| total | 2170 | 1907 (88.9% without the 24 tautologies) | 162 | 101 |

None of the 80 FALSE formulas of the soundness run passed, and the deadlock encoding agrees with an
explicit product check on all 1,173 formulas small enough to decide that way. All 1907 rankings in
`rankings/` re-verify from disk (`reverify.py`).

## Farkas JSON export (`export_farkas.py`)

The folding prover (`zkmc-symbolic/folding`) consumes JSON of shape `FullZkpData` — the same shape
`zkfarkas` emits. `export_farkas.py` bridges the committed `rankings/gc/*.gc` files to that format
for the bench: one worker per file (same `Process`/`Queue` pattern as `reverify.py`), per-worker
`RLIMIT_AS` for OOM isolation, timeout-kill, JSONL journal + resume.

A JSON is only written if the prover can range-check every obligation against
`BIG_M = 2^31 - 1` (`big_M` in the prover): `A_s`, `b_s` in `[-M, M]` and `lambda_s`, `mu_s`,
`v = -b_s^T lambda_s - h_p^T mu_s - 1` in `[0, M]`. The Farkas solver bounds the witness accordingly,
but `A_s`/`b_s` are instance data, so files exceeding them get status `out-of-range: [(obligation,
quantity), ...]` instead. This applies to the 7 `GPPP-PT-C0010N1000000000` rankings, whose
initial markings reach 4·10^9.

```bash
uv run python benchmarks/mcc/export_farkas.py                           # full export (1907 files)
uv run python benchmarks/mcc/export_farkas.py --range 0..100            # first 100 files
uv run python benchmarks/mcc/export_farkas.py --exams LTLCardinality --random 50
uv run python benchmarks/mcc/export_farkas.py --max-obligations 1000    # calibration set
uv run python benchmarks/mcc/export_farkas.py --dry-run                 # print selection, no work
```

Selection filters compose (AND):

| Flag | Effect |
|---|---|
| `[paths]` | positional files/dirs; default `benchmarks/mcc/rankings/gc` |
| `--range START..END` | half-open Rust-style slice of the canonical sorted list |
| `--exams LTLCardinality,LTLFireability` | keep only files matching the given exams |
| `--min/--max-obligations N` | read from `rankings/index.csv` — zero parsing |
| `--random N` | deterministic random sample (seed 0), applied last |
| `--dry-run` | print selection + obligations and exit |

Other flags:

| Flag | Default | Effect |
|---|---|---|
| `--jobs N` / `--jobs all` | `2` | worker count; `all` = `min(cpu, RAM/mem-limit)` |
| `--timeout` | `1200` | per-file wall timeout (s) |
| `--mem-limit-mb` | `3000` | `RLIMIT_AS` per worker |
| `--out-dir` | `benchmarks/corpus/mcc2025/farkas-json/` | output directory for `.json` files |
| `--out CSV` | — | manifest CSV; `<out>.jsonl` journal written automatically for resume |
| `--pretty` | compact | emit zkfarkas-compatible spaced JSON (default is compact, ~15% smaller) |
| `--retry-status` | `timeout,error,crashed,memory` | on resume, re-run files whose last status **starts with** one of these |
| `--force` | — | ignore journal, re-export everything |

### Alignment with the folding bench's range selector

The Rust bench (`prove_and_verify_full.rs`) sorts its `ZKMC_BENCH_FILES` list and accepts the
same `START..END` syntax after `--`. Both sides sort over identical stems (`.gc` vs `.json`, same
prefix), so ranges align **iff the JSON dir contains exactly one JSON per GC**. To keep indices
stable across machines and reruns (and so the bench never sees a missing file), build
`ZKMC_BENCH_FILES` from the manifest's `pass` rows rather than from `find`:

```bash
# After the export campaign, write a frozen file list from the manifest:
uv run python -c "
import csv
with open('benchmarks/corpus/mcc2025/farkas-json/export.csv') as fh:
    rows = [r['file'] for r in csv.DictReader(fh) if r['status'] == 'pass']
print(','.join(sorted(rows)))
" > /tmp/mcc_files.txt
export ZKMC_BENCH_FILES=$(cat /tmp/mcc_files.txt)

# Then on each machine, slice the shared list:
cargo bench --bench prove_and_verify_full -- 0..954      # machine A
cargo bench --bench prove_and_verify_full -- 954..       # machine B
```

## TODO

- [ ] **Stats program** — read all exported `.json` files and report, per file and in aggregate:
  unique `A_s`, unique `b_s`, unique `(A_s, b_s)` pairs, distinct `program_transition` values
  (i.e. net commands), plus the bench-relevant dimensions `max_m`, `max_n`, `aggregation_size`,
  `party_cap`. The point is twofold: (a) estimate total prover time from calibration runs on
  size buckets, and (b) flag files where `max_n >= N` (the old bench assert) or other known
  pre-flight failures before the bench sees them.
- [ ] **Driver harness** — a wrapper that coordinates the exporter + bench across multiple
  machines: (i) generates the canonical file list, (ii) slices it per machine, (iii) invokes
  `export_farkas.py --range k..m` and the bench on each host, (iv) collects timing logs and
  produces summary tables. Probably a Python script in `encoder/benchmarks/mcc/` or a Makefile.
- [ ] **32-bit bounds pre-flight** — the bench's range proofs require entries in
  `[-(2^31 - 1), 2^31 - 1]` (A/−b) and `[0, 2^31 - 1]` (λ/μ, α+β−1). The exporter already
  catches unsatisfiable obligations and empty matrices, but it does not yet pre-emptively
  reject files whose entries exceed these bounds (which would panic the bench's `.unwrap()`).
  Add this check to `export_farkas.py`'s worker or to the stats program.
- [ ] **Upstream `--emit-json` to `reverify.py`** — once the exporter stabilises, offer paberr
  a `--emit-json DIR` flag on `reverify.py` (same worker pattern, just adds the Farkas dump to
  the existing verify pass). Reduces tooling surface area.
