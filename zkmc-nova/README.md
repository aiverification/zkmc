# ZKMC–Sonobe backend

This repository connects the official **Zero-Knowledge Model Checking (ZKMC)** obligation generator to a Nova folding backend built with Sonobe. It reproduces the paper’s `exb_i1a2` benchmark and proves all 217 symbolic obligations in one incrementally verifiable computation.

## What is implemented

- The official ZKMC encoder is pinned and run on the exponential-backoff model with `initialDelay=1` and `maxAttempts=2`.
- `zkfarkas` generates the complete symbolic obligation set and integer Farkas witnesses.
- `scripts/adapt_official.py` validates the upstream JSON, converts it into the local fixed-shape format, and derives the required circuit dimensions and bound.
- A plain Rust checker verifies every obligation before circuit execution.
- An Arkworks R1CS circuit proves one padded Farkas obligation per step.
- Nova folds all obligations sequentially, and Sonobe’s IVC verifier checks the final folded proof.

## End-to-end flow

```text
Guarded-command model + Büchi automaton + ranking function
                         │
                         ▼
              Official ZKMC `zkfarkas`
                         │
                         ▼
          217 symbolic obligations and witnesses
                         │
                         ▼
     Validate, normalize, pad, and generate dimensions
                         │
                         ▼
       Plain check → R1CS check → Nova folding
                         │
                         ▼
                 Sonobe IVC verification
```

## Proved relation

For every obligation, the circuit enforces:

```text
A_s^T λ = -G_p^T μ
δ = -b_s^T λ - h_p^T μ - 1
λ, μ, δ ∈ [0, M]
A_s, b_s, G_p, h_p ∈ [-M, M]
```

Here, `λ` and `μ` are Farkas multipliers: compact certificates that the corresponding system of inequalities has no violating state.

Signed values use sign-and-magnitude encoding. Zero padding gives every obligation the same R1CS shape, while range checks prevent values from exceeding the generated benchmark bound.

## Nova state

The recursive state is:

```text
[processed_count, total_count, model_tag, certificate_tag, bound]
```

Each fold checks the expected obligation index, preserves the batch metadata, proves one Farkas relation, and increments `processed_count`.

For the official benchmark, verification completed with:

```text
217 obligations: 2 initial, 170 transition, 45 fair
fixed shape:      28 secret rows, 11 public rows, 10 columns
bound:            217
range bits:       8
final state:      [217, 217, 4157934096332607709,
                   2495031230553792324, 217]
```

## Run the official benchmark

```bash
cd ~/Code/zkmc
source "$HOME/.cargo/env"
chmod +x scripts/run_exb_i1a2.sh
./scripts/run_exb_i1a2.sh
```

A successful run confirms:

```text
217 obligations folded
ivc verification passed
final state begins with [217, 217, ...]
```

The complete run log is written to:

```text
artifacts/exb_i1a2_phase2.log
```

## Development commands

```bash
cargo test
cargo run --release -- inspect path/to/obligations.json
cargo run --release -- plain path/to/obligations.json
cargo run --release -- circuit path/to/obligations.json
cargo run --release -- nova path/to/obligations.json
cargo run --release -- all path/to/obligations.json
```

`plain` checks the integer relation directly. `circuit` tests the same relation inside Arkworks without Nova. `nova` performs the recursive folding and final IVC verification.

## Source layout

```text
src/main.rs                 CLI dispatch
src/lib.rs                  crate exports and shared result type
src/config.rs               fixed configuration interface
src/generated_config.rs     benchmark-generated dimensions and bounds
src/model.rs                batch and obligation data structures
src/input.rs                JSON parsing, validation, and padding
src/checker.rs              plain integer Farkas checks
src/circuit/input.rs        circuit inputs and witness allocation
src/circuit/constraints.rs  fixed-shape R1CS relation
src/runner.rs               inspection, circuit, and Nova execution
src/tests.rs                positive and negative tests

scripts/adapt_official.py   upstream JSON adapter and dimension scan
scripts/run_exb_i1a2.sh     complete reproducible benchmark pipeline
scripts/solve_farkas.py     normalized standalone witness generator
scripts/check_structure.sh  source-layout and line-count checks
```

All hand-written source files remain below 500 lines. `Cargo.lock` is generated and preserved to keep the Sonobe and Arkworks dependency graph reproducible.

## Current limitations

- `model_tag` and `certificate_tag` enforce continuity across folds but are not yet cryptographic commitments to the secret model and full certificate set.
- Changing the generated dimensions or bound changes the circuit shape and requires fresh Nova preprocessing.

Do not change the pinned Sonobe revision, upstream ZKMC commit, or `Cargo.lock` without retesting the complete 217-obligation run.