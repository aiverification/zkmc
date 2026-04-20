# Benchmarks

pytest-benchmark harnesses for `zkverify`, `zkfarkas`, and `zkexplicit`. Benchmark programs live in [`../examples/`](../examples/); this directory contains only the measurement code and its configuration.

## Running

```bash
# All benchmarks
uv run pytest benchmarks/ --benchmark-only

# Filter by benchmark name / tag
uv run pytest benchmarks/ --benchmark-only -k "counter_small"
uv run pytest benchmarks/ --benchmark-only -k "small"
uv run pytest benchmarks/ --benchmark-only -k "paper"

# Save / compare
uv run pytest benchmarks/ --benchmark-only --benchmark-save=before
# …make a change…
uv run pytest benchmarks/ --benchmark-only --benchmark-compare=before

# Export JSON for further analysis
uv run pytest benchmarks/ --benchmark-only --benchmark-json=results.json
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
