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
