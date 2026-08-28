# zkmc-symbolic

A Rust prototype implementation of ZKMC's symbolic non-folding approach.

## Installation
- Install [Rust](https://rust-lang.org/)
- Clone this repo
- `cd zkmc-symbolic`

## Running the code
There are two ways to run this prototype:

- `cargo run --release` - this will execute `main.rs`, which contains a simple example of running ZKRP on a toy matrix, as well as testing zkMatrix (that a.b=c for some toy matrices).
- `cargo bench` - this will execute all of the benchmarks in `benches/`, on all inputs in `input/` (by default). Note: for weaker/slower systems, such as everyday laptops, some of these inputs may take a while to execute. Instead, you may wish to choose an individual benchmark type to run (all of which run on all `input/` files by default):
  - `cargo bench --bench bench_testing` - a benchmark used to measure and output the number of unique instances of `A`, `-b`, etc. in a given input file. Used for debugging and testing.
  - `cargo bench --bench benchmark_full_parallel_cached` - full end-to-end benchmark. Prover caches `A, -b` proofs only, no verifier cache.
  - `cargo bench --bench benchmark_fpc_updated` - full end-to-end benchmark. Prover caches `A, -b` proofs, verifier caches `A, -b` verifications.
  - `cargo bench --bench benchmark_more_cache` - full end to end benchmark. Prover caches `A, -b, mu, lambda, alpha, beta` proofs, verifier caches likewise. **There are potential information leakage and security concerns with this method**.

Running any of these will install the required Rust toolchain (if not already installed), and build the prototype, which may take a few minutes.

## Code structure
- `benches/` - Contains `benchmark_full_parallel.rs`, which runs the full ZKP on input files specified in this Rust file, and outputs timings.
- `data/public/` and `data/private/` - **Required folders** for zkMatrix to populate and use during execution.
- `input/` - Contains various input files for testing and benchmarking.
- `output/` - Directory for timing outputs to be written to during benchmarking.

### `src`
- `lib.rs` - Makes code available to use within this crate.
- `main.rs` - Contains a toy example of ZKRP running, and zkMatrix running.
- `range_proof.rs` - Implementation of range proof with support for proving and verifying $x \in [0, b]$.
- `utils.rs` - Miscellaneous utility functions, such as casting between `bls12_381` implementations and matrix operations.
- `zkmmeq.rs` - Implementation of ZKEQ as specified in the ZKMC paper.
- `zkp.rs` - Implementation of full ZKP as specified in the ZKMC paper.
- `zkp_cache.rs` - Implementation of full ZKP as specified in the ZKMC paper, with support for cached proofs and verifications.
- `zkrp.rs` - Implementation of ZKRP as specified in the ZKMC paper.
