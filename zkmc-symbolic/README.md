# zkmc-symbolic

A Rust prototype implementation of ZKMC.

## Installation
- Install [Rust](https://rust-lang.org/)
- Clone this repo
- `cd zkmc-symbolic`

## Running the code
There are two ways to run this prototype:

- `cargo run --release` - this will execute `main.rs`, which contains a simple example of running ZKRP on a toy matrix, as well as testing zkMatrix (that a.b=c for some toy matrices).
- `cargo bench --message-format=json` - this will execute `benches/benchmark_full_parallel.rs`, which by default runs the full ZKP on the input files found in `inputs/` and outputs timing data to `outputs/`. Note: for weaker/slower systems, such as everyday laptops, some of these inputs may take a while to execute.

Running either of these will install the required Rust toolchain (if not already installed), and build the prototype, which may take a few minutes.

## Code structure
- `benches/` - Contains `benchmark_full_parallel.rs`, which runs the full ZKP on input files specified in this Rust file, and outputs timings.
- `data/public/` and `data/private/` - **Required folders** for zkMatrix to populate and use during execution.
- `input/` - Contains various input files for testing and benchmarking.
- `output/` - Directory for timing outputs to be written to during benchmarking.

### `src`
- `lib.rs` - Makes code available to use within this crate.
- `main.rs` - Contains a toy example of ZKRP running, and zkMatrix running.
- `range_proof.rs` - Implementation of range proof with support for proving and verifying $x \in [0, b]$.
- `schnorr.rs`
- `utils.rs` - Miscellaneous utility functions, such as casting between `bls12_381` implementations and matrix operations.
- `zkp.rs` - Implementation of full ZKP as specified in the ZKMC paper.
- `zkrp.rs` - Implementation of ZKRP as specified in the ZKMC paper.
