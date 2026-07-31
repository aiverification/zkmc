# ZKMC–Sonobe backend

This repository converts symbolic ZKMC obligations into one fixed R1CS step circuit, folds the ordered steps with Nova, and compresses the completed IVC computation with Sonobe's **offchain Nova decider**.

## Implemented flow

```text
guarded-command benchmark
        ↓
official ZKMC encoder and Farkas witnesses
        ↓
normalized, zero-padded obligations
        ↓
plain integer verification
        ↓
fixed R1CS Farkas circuit
        ↓
ordered Poseidon model/certificate commitments
        ↓
Nova folding and IVC verification
        ↓
offchain Groth16/KZG decider proof
        ↓
canonical serialization, reload, and verification
```

Each step enforces:

```text
A_s^T lambda = -G_p^T mu
delta = -b_s^T lambda - h_p^T mu - 1
delta, lambda, mu in [0, M]
coefficients in [-M, M]
```

The recursive state binds the processed count, total count, model commitment, certificate commitment, running ordered digests, bound, and model-blinding commitment.

## Why the offchain decider

This is a local research prover, not an Ethereum verifier. The previous BN254/Grumpkin `decider_eth` path produced a valid Nova IVC instance but an unsatisfied Ethereum-specific decider circuit. The replacement follows Sonobe's dedicated offchain design: an MNT4-298/MNT6-298 curve cycle, KZG commitments on both curves, and one Groth16 proof per decider circuit.

The setup script pins Sonobe at `9b7dd34` and applies one narrow source change: it derives canonical serialization for Sonobe's offchain proof container. The cryptographic computation and verification logic are unchanged.

## Setup

```bash
cd ~/Code/zkmc
chmod +x scripts/*.sh scripts/*.py
./scripts/setup_ubuntu.sh
source "$HOME/.cargo/env"
```

For an existing environment:

```bash
./scripts/setup_sonobe_offchain.sh
cargo generate-lockfile
```

## Small complete run

```bash
./scripts/smoke.sh
```

A successful run prints:

```text
ivc verification passed
in-memory offchain decider verification passed
serialized offchain decider verification passed
decider proof verification passed
```

## Official `exb_i1a2` run

```bash
./scripts/run_exb_i1a2.sh
```

The script requires exactly 217 official obligations and stores:

```text
artifacts/exb_i1a2_phase3/
├── statement.json
├── decider_proof.bin
├── decider_verifier.bin
├── decider_public.bin
└── manifest.json
```

## Source layout

```text
src/main.rs                 CLI dispatch
src/model.rs                obligation and batch types
src/input.rs                JSON parsing and padding
src/checker.rs              plain Farkas verification
src/commitment.rs           ordered Poseidon commitments
src/circuit/input.rs        circuit witness allocation
src/circuit/hash.rs         in-circuit digest updates
src/circuit/constraints.rs  fixed R1CS step relation
src/runner.rs               plain, circuit, and Nova flow
src/decider.rs              offchain decider and artifacts
src/statement.rs            public statement matching
src/artifact.rs             canonical file encoding
```

All hand-written files remain below 500 lines. The MNT4-298/MNT6-298 cycle is appropriate for functional research replication; it is not presented as a production deployment parameter set.
