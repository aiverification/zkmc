//! Dispatches the complete ZKMC proving and verification CLI.

use std::{env, io, path::PathBuf};
use zkmc::{
    config::DEFAULT_INPUT,
    decider::{default_artifact_dir, verify_artifact_dir},
    input::load_batch,
    runner::{inspect, run_all, run_decider, run_nova},
    statement::{write_statement, DEFAULT_STATEMENT},
    AppResult,
};

/// Prints supported command line invocation modes.
fn usage() {
    println!("usage:");
    println!("  zkmc commit [json] [statement-json]");
    println!("  zkmc [inspect|nova] [json]");
    println!("  zkmc [decider|all] [json] [artifact-dir] [statement-json]");
    println!("  zkmc verify <artifact-dir> <statement-json> <trusted-verifier-bin>");
}

/// Loads only the public artifacts required by the standalone verifier.
fn run_verify(args: &[String]) -> AppResult<()> {
    let artifact_dir = args
        .get(2)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "missing artifact directory"))?;
    let statement_path = args
        .get(3)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "missing public statement"))?;
    let verifier_path = args.get(4).ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "missing trusted verifier parameters",
        )
    })?;
    verify_artifact_dir(artifact_dir, statement_path, verifier_path)?;
    Ok(())
}

/// Loads inputs and dispatches selected mode.
fn main() -> AppResult<()> {
    let args = env::args().collect::<Vec<_>>();
    let mode = args.get(1).map(String::as_str).unwrap_or("all");
    if mode == "verify" {
        return run_verify(&args);
    }

    let input_path = args.get(2).map(String::as_str).unwrap_or(DEFAULT_INPUT);
    let batch = load_batch(input_path)?;

    match mode {
        "commit" => {
            let path = args
                .get(3)
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from(DEFAULT_STATEMENT));
            write_statement(&batch, path)?;
        }
        "inspect" => inspect(&batch),
        "nova" => run_nova(&batch)?,
        "decider" | "all" => {
            let artifact_dir = args
                .get(3)
                .map(PathBuf::from)
                .unwrap_or_else(default_artifact_dir);
            let statement_path = args
                .get(4)
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from(DEFAULT_STATEMENT));
            if !statement_path.exists() {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    format!(
                        "missing public statement {}; run `zkmc commit` first",
                        statement_path.display()
                    ),
                )
                .into());
            }
            if mode == "decider" {
                run_decider(&batch, artifact_dir, statement_path)?;
            } else {
                run_all(&batch, artifact_dir, statement_path)?;
            }
        }
        _ => usage(),
    }
    Ok(())
}
