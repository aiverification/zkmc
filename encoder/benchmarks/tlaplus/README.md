# TLA+ benchmark

Runs zkmc on the finite models of the [TLA+ Examples](https://github.com/tlaplus/Examples)
repository: every model that TLC checks exhaustively and successfully (the repository's
`manifest.json` files record mode, result and state count), with its invariants and temporal
properties as instances.

TLA+ is far more expressive than linear guarded commands, but a *finite* TLC model is not:
`tla2gc.py` grounds the specification over the model's constants and encodes every variable
as integer slots, so that each action becomes a set of linear guarded commands.

## Translation (`tla2gc.py`)

1. **TLC** (`tlc/Dockerfile`, pinned TLA+ tools) explores the model without symmetry reduction,
   views, invariants or properties, and dumps all reachable states.
2. **Layout** (`layout.py`): the values each variable takes in those states decide its
   encoding: integers stay integers, booleans become 0/1, strings and model values (and
   anything without further structure) become indices into a table, functions and records get
   one slot per domain element, sequences a length slot plus one slot per position, and sets one
   0/1 slot per element of their universe. Slot ranges become `type` declarations.
3. **Grounding** (`tla_eval.py`): the next-state relation is evaluated symbolically over the
   slots, the way TLC enumerates successors: conjunctions left to right, `x' = e` and
   `x' \in S` assign, and disjunctions, `\E` over constant sets, IF and CASE branch. Values that
   depend on the state are linear expressions over slots where possible; otherwise (a function
   applied to a symbolic argument, nonlinear arithmetic, ...) they are split into cases over
   the values the slots take. Every branch becomes guarded commands whose guards are the DNF of
   its condition (`tla_sym.py`, using the slot ranges to simplify). As in TLC, a successor must
   satisfy the model's `CONSTRAINT`s and the step its `ACTION_CONSTRAINT`s.
4. **Initial states**: the box of their slot values when exact, else a polyhedron (affine hull
   plus pairwise bounds, checked exact with Z3), else a `boot` slot whose commands pick one
   initial state each (the properties then start in the second state, `X φ`).
5. TLA+ behaviours may stutter at any time, so there is an unconditional stutter command.

The commands coincide with the TLA+ next-state relation on all states in the slot ranges, so the
`.gc` program and the TLC model have the same reachable states and transitions; the
`translate` phase checks this against TLC's state graph: the `.gc` program must reach exactly
TLC's states and have exactly its transitions between distinct states (`mismatch` otherwise). Translation fails (`unsupported`) on constructs outside the fragment (unbounded
CHOOSE or quantifiers that TLC could not enumerate either, `[][A]_v` properties, refinement
properties, ...) or when a case split, DNF, or the number of commands exceeds its budget.

## Properties

- Each `INVARIANT` p becomes the LTL property `G p`. Invariants that hold on every state of
  the slot ranges (typically `TypeOK`) are `tautology` and not instances.
- Each temporal `PROPERTY` becomes LTL over state predicates (`[]`, `<>`, `~>`, boolean
  structure, IF, and `\A`/`\E` over constant sets), assuming the specification's `WF`/`SF`
  conditions: an auxiliary slot `act` records which fairness-relevant action the last step
  took, and `WF_v(A)` becomes `GF !ENABLED<<A>>_v | GF act_A` (`FG` for `SF`). A property is
  split into its top-level conjuncts (e.g. one instance per process for `\A p : P(p) ~> Q(p)`).
  Each instance assumes only a minimal subset of the fairness conditions: conditions are dropped
  greedily while the explicit product check says the property still holds (`--all-fairness`
  keeps them all). Assuming less is a stronger property, so its proof proves the original, and
  the automata get much smaller (e.g. 6 to 3 conditions and about 1650 to 200 transitions for
  `SimpleAllocator`).
- Spot (`ltl2tgba -B`) translates the negated property to a Büchi automaton, whose edge labels
  are lowered to guards with the slot ranges, and the automaton is written into the `.gc` file
  explicitly (no Spot needed downstream).

## Setup

```bash
docker build -t zkmc-tlc:local benchmarks/tlaplus/tlc
docker build -t zkmc-spot:local benchmarks/mcc/spot
export ZKTERM_LTL2TGBA=$PWD/benchmarks/mcc/spot/ltl2tgba
git clone https://github.com/tlaplus/Examples benchmarks/corpus/tlaplus-examples
git -C benchmarks/corpus/tlaplus-examples checkout bfcdea6a906fbad2cb6025c909377d1fb6297872
```

## Runs

```bash
uv run --group tlaplus python benchmarks/tlaplus/run_tlaplus.py translate --max-states 200000 --jobs 3 \
    --timeout 3600 --tlc-timeout 600 --prop-timeout 900
uv run --group tlaplus python benchmarks/tlaplus/run_tlaplus.py prove --explicit --houdini --samples 50000 \
    --relational --minimize --cegis-fallback --synth-budget 120 --timeout 300 --jobs 2 --mem-limit-mb 3000
uv run --group tlaplus python benchmarks/tlaplus/summarize.py
uv run --group tlaplus python benchmarks/tlaplus/bundle.py --out benchmarks/tlaplus/rankings
```

TLC runs in a container with its state files in a 2 GB tmpfs and stops once its state graph
dump exceeds 1 GB (models without a known state count, e.g. failing ones, can be huge).

`translate` writes `<work>/models.csv`, `<work>/instances.csv` and one `.gc` file per instance
to `<work>/gc/` (`--work`, default `benchmarks/corpus/tlaplus`); TLC results are cached in
`<work>/tlc/`. `prove` writes `<work>/prove.csv` and the verified files (with rankings) to
`<work>/rankings/`. `--explicit` first checks each instance on the explicit product (it must
hold, since TLC verified the model). `--relational` adds affine-hull and pairwise-bound guesses
to Houdini (`benchmarks/mcc/invariants.py`), which TLA+ models need far more often than P/T
nets. Both phases are journaled; rerunning resumes (`--retry-status` reruns given statuses).
`bundle.py` collects the verified files into `rankings/` (see `rankings/README.md`).
