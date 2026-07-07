# Examples

Reference `.gc` programs used across the documentation, the benchmarks, and for hands-on exploration of the language.

| File | Size | Purpose |
|------|------|---------|
| [`example.gc`](example.gc) | tiny | Minimal walk-through: one variable, one fair self-loop, one-case ranking. Used by [`LANGUAGE.md`](../LANGUAGE.md) as the introductory example. |
| [`counter_simple.gc`](counter_simple.gc) | small | Simple counter from `0` to `maxVal`. Baseline for the benchmark suite. |
| [`counter_synth.gc`](counter_synth.gc) | tiny | Counter with **no** `rank(...)` block — the ranking is synthesized (`zksynth` / `zkverify --synthesize`). |
| [`round-robin.gc`](round-robin.gc) | medium | Round-robin scheduling pattern. |
| [`exp_backoff_state_opt_small.gc`](exp_backoff_state_opt_small.gc) | small | Exponential backoff with state-based ranking, 2 attempts. |
| [`exp_backoff_ltl.gc`](exp_backoff_ltl.gc) | small | Same model as above, but the property is written in LTL (`spec: "G F !waiting"`) and the automaton is derived via Spot. Requires `ltl2tgba` on `PATH`. |
| [`exp_backoff_state_opt.gc`](exp_backoff_state_opt.gc) | medium | Same family, 3 attempts, parameterised by `initialDelay`. |
| [`exp_backoff_guard_opt.gc`](exp_backoff_guard_opt.gc) | medium | Exponential backoff with guard-based ranking; parameterised by `initialDelay` and `maxAttempts`. |
| [`dhcp.gc`](dhcp.gc) | large | Realistic DHCP-client model with constants, type annotations, and a ranking function over seven protocol states. |

All files use the language described in [`../LANGUAGE.md`](../LANGUAGE.md).

## Running an example

```bash
uv run zkterm    examples/example.gc      # encode to matrices
uv run zkverify  examples/dhcp.gc         # verify termination (may need --skip-validation)
uv run zkfarkas  examples/dhcp.gc --pretty  # Farkas duals as JSON
uv run zkexplicit examples/counter_simple.gc --pretty  # explicit-state + embeddings
uv run zkltl     examples/exp_backoff_ltl.gc   # derive the Büchi automaton from the LTL spec (needs Spot)
uv run zksynth   examples/counter_synth.gc     # synthesize the ranking function
uv run zkverify --synthesize examples/counter_synth.gc  # synthesize + verify
```

Use `--const NAME=VALUE` to override constants without editing the file, e.g.:

```bash
uv run zkverify examples/exp_backoff_guard_opt.gc --const initialDelay=8 --const maxAttempts=2
```

## Benchmarks

Files here are picked up by the benchmark harness via `program_loader` — see [`../benchmarks/README.md`](../benchmarks/README.md) for how to add a benchmark case pointing at a file in this directory.
