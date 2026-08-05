# Benchmarks

pytest-benchmark harnesses for `zkverify`, `zkfarkas`, and `zkexplicit`. Benchmark programs live in [`../examples/`](../examples/); this directory contains only the measurement code and its configuration.

## ITS termination-benchmark suite (KoAT `.koat`)

`run_its.py` runs KoAT's `.koat` integer-transition-system benchmarks through import → synthesize → verify (symbolic path, no bounds) and reports coverage statistics with a categorized reason per file. The corpus (KoAT's Complexity_ITS, 838 `.koat` from TermComp's TPDB) is fetched by `fetch_corpus.sh` into a gitignored `corpus/` (not committed).

```bash
bash fetch_corpus.sh                    # one-time download of 838 .koat into corpus/
uv run python run_its.py \
    --corpus corpus/Complexity_ITS --jobs 8 --timeout 20 --out stats.csv
```

Each file is classified: `pass` / `no-ranking` (search exhausted — needs multiphase/invariants) / `unsupported-comn` (recursion) / `unsupported-nonlinear` / `unsupported-parse` / `timeout` / `error`. `--emit-farkas DIR` dumps the Farkas-dual JSON for passing files (input for the `zkmc-symbolic` prover). See [`REPORT.md`](REPORT.md) for the current coverage snapshot.

Use the argument `--max-mode-vars` to control whether the ranking function search strategy uses pair partitions (set to `2`) or not (set to `1`). Default is `2`.

## Running

```bash
# All benchmarks
uv run pytest . --benchmark-only

# Filter by benchmark name / tag
uv run pytest . --benchmark-only -k "counter_small"
uv run pytest . --benchmark-only -k "small"
uv run pytest . --benchmark-only -k "paper"

# Save / compare
uv run pytest . --benchmark-only --benchmark-save=before
# …make a change…
uv run pytest . --benchmark-only --benchmark-compare=before

# Export JSON for further analysis
uv run pytest . --benchmark-only --benchmark-json=results.json
```

## Adding a benchmark

1. Drop your program into [`../examples/`](../examples/).
2. Add a `BenchmarkCase` entry to [`benchmark_config.py`](benchmark_config.py):

   ```python
   BenchmarkCase(
       name="my_protocol",
       program_file="my_protocol.gc",        # relative to examples/
       const_overrides={"maxRetries": 3},
       bounds=["status:0:3", "delay:0:100"], # zkexplicit bounds; None uses type annotations
       tags=["custom"],
       description="My protocol"
   )
   ```

Benchmarks pick up `BenchmarkCase` entries automatically; `program_loader` in [`conftest.py`](conftest.py) resolves `program_file` against `examples/`.

## Bounds and tags

- **Bounds** (for `zkexplicit`): `"var:min:max"`, inclusive. Pick a range slightly wider than what the program should ever reach so any bug that escapes the intended domain shows up as a violation rather than silently staying in bounds.
- **Tags** filter with `-k`. Common ones used today: `baseline`, `small`, `medium`, `large`, `paper`, `custom`, plus family names like `exp_backoff`.

## Complexity reminders

- State enumeration: `O(∏ rᵢ)` for variables with ranges `rᵢ`.
- `zkexplicit` violation checking: `O((∏ rᵢ)² · |δ|)` — quadratic in the state space, linear in the automaton size.
- `zkverify` is typically dominated by Z3 time; parsing/encoding is negligible.
