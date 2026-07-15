//! Dispatches the ZKMC Phase Two CLI.

use std::env;
use zkmc::{
    checker::run_plain,
    config::DEFAULT_INPUT,
    input::load_batch,
    runner::{inspect, run_circuit, run_nova},
    AppResult,
};

/// Prints supported command line invocation modes.
fn usage() {
    println!("usage: cargo run --release -- [inspect|plain|circuit|nova|all] [json]");
}

/// Loads inputs and dispatches selected mode.
fn main() -> AppResult<()> {
    let args = env::args().collect::<Vec<_>>();
    let mode = args.get(1).map(String::as_str).unwrap_or("all");
    let path = args.get(2).map(String::as_str).unwrap_or(DEFAULT_INPUT);
    let batch = load_batch(path)?;

    match mode {
        "inspect" => inspect(&batch),
        "plain" => run_plain(&batch)?,
        "circuit" => run_circuit(&batch)?,
        "nova" => run_nova(&batch)?,
        "all" => {
            inspect(&batch);
            run_plain(&batch)?;
            run_circuit(&batch)?;
            run_nova(&batch)?;
        }
        _ => usage(),
    }
    Ok(())
}
