# zkmc-explicit

A Rust prototype implementation of ZKMC.

## Installation

- Install [Rust](https://rust-lang.org/)
- Clone this repo
- `cd zkmc-explicit`

## Running the code

To run this code, use `cargo bench --message-format=json`. This will execute `benches/benchmark_full_parallel.rs`, which by default runs the full ZKP on the input files found in `inputs/` and outputs timing data to `outputs/`. Note: for weaker/slower systems, such as everyday laptops, some of these inputs may take a while to execute.

Running this will install the required Rust toolchain (if not already installed), and build the prototype, which may take a few minutes.

## Code structure

- `benches/` - Contains `benchmark_full_parallel.rs`, which runs the full ZKP on input files specified in this Rust file, and outputs timings.
- `input/` - Contains various input files for testing and benchmarking.
- `output/` - Directory for timing outputs to be written to during benchmarking.

### `src`

- `interpolation.rs` - Holds method to use IFFT to interpolate polynomial for use in ZKP.
- `lib.rs` - Makes code available to use within this crate.
- `zkp.rs` - Implementation of full ZKP as specified in the ZKMC paper.
