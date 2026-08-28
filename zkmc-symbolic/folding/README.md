# zkmc-symbolic folding
A Rust prototype implementation of ZKMC's symbolic folding approach.

## Installation
- Install [Rust](https://rust-lang.org/)
- Clone this repo
- `cd zkmc-symbolic/folding`

## Running the code
There is one way to run this prototype.
- `cargo bench` will run the benchmark `prove_and_verify_full` - the full protocol over the files listed in `CANDIDATE_FILES`, writing one line per run to `outputs/`. **This is the benchmark used for data generation for the ZKMC paper**.

Additionally, there are some tests for correctness included.
- `cargo test` runs the correctness suite:
  - `tests/primitives.rs` - each optimised primitive (G1 column commitments, the multi-scalar and fixed-base paths, the dense folds, the multiplicative `r^k` table) is checked against the implementation it replaced.
  - `tests/end_to_end.rs` - an honest proof over a small synthetic instance must verify; tampered witnesses, tampered commitments and out-of-range values must each be rejected. `fold_prover_verifier_agree` pins that the verifier reconstructs exactly the folded commitments the prover proved against.

Running any of these will install the required Rust toolchain (if not already installed), and build the prototype, which may take a few minutes.

## Code structure
- `benches/` - Contains `prove_and_verify_full.rs`, which runs the full ZKP on input files specified in this Rust file, and outputs timings.
- `data/public/` and `data/private/` - **Required folders** for zkMatrix to populate and use during execution.
- `input/` - Contains various input files for testing and benchmarking.
- `outputs/` - Directory for timing outputs to be written to during benchmarking.

### `src`
- `commit.rs` - Copies of zkMatrix's commitment mechanisms, minus printing and with some minor tweaks (often performance-related).
- `folding.rs` - Contains functions related to computing the required folded terms for both prover and verifier (and related/supporting functionality).
- `lib.rs` - Makes code available to use within this crate.
- `range_proof.rs` - Implementation of range proof with support for proving and verifying $x \in [0, b]$.
- `zkmmeq.rs` - Implementation of ZKEQ as specified in the ZKMC paper.
- `zkp.rs` - Implementation of full ZKP as specified in the ZKMC paper.
- `zkrp.rs` - Implementation of ZKRP as specified in the ZKMC paper.
- `utils/`
  - `curve_utils.rs` - Utility functions related to the `bls12_381` curve used in the implementation.
  - `msm.rs` - Utility functions related to multi-scalar multiplication.
  - `plain_utils.rs` - Utility functions used in the clear (or with "plain" data, e.g. matrix padding).
  - `public_exponent_schedule.rs` - Utility functions related to getting the public exponent schedule, currently limited to the Salem-Spencer Schedule.
  - `zk_utils.rs` - Utility functions related to handling zk data structures (e.g. zkMatrix's `Mat` types) and folding.