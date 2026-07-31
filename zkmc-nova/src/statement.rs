//! Manages the public precommitment statement.

use crate::{artifact::read_json, model::Batch, AppResult};
use serde::{Deserialize, Serialize};
use std::{fs, io, path::Path};

pub const DEFAULT_STATEMENT: &str = "artifacts/statement.json";
pub const BUNDLED_STATEMENT: &str = "statement.json";

/// Records public values fixed before proving.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct CommitmentStatement {
    pub schema_version: u32,
    pub benchmark: String,
    pub obligation_count: usize,
    pub bound: u64,
    pub model_blinding_commitment: String,
    pub model_commitment: String,
    pub certificate_commitment: String,
}

/// Builds the public statement for one batch.
pub fn statement_from_batch(batch: &Batch) -> CommitmentStatement {
    CommitmentStatement {
        schema_version: 1,
        benchmark: batch.benchmark.clone(),
        obligation_count: batch.obligations.len(),
        bound: batch.bound,
        model_blinding_commitment: batch.model_blinding_commitment.to_string(),
        model_commitment: batch.model_commitment.to_string(),
        certificate_commitment: batch.certificate_commitment.to_string(),
    }
}

/// Writes a statement that can be published.
pub fn write_statement(batch: &Batch, path: impl AsRef<Path>) -> AppResult<()> {
    let path = path.as_ref();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(
        path,
        serde_json::to_string_pretty(&statement_from_batch(batch))? + "\n",
    )?;
    println!("public commitment statement: {}", path.display());
    Ok(())
}

/// Loads one public statement without any private prover input.
pub fn load_statement(path: impl AsRef<Path>) -> AppResult<CommitmentStatement> {
    let statement: CommitmentStatement = read_json(path)?;
    if statement.schema_version != 1
        || statement.benchmark.trim().is_empty()
        || statement.obligation_count == 0
        || statement.bound == 0
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid public commitment statement",
        )
        .into());
    }
    Ok(statement)
}

/// Loads and matches a previously fixed statement.
pub fn validate_statement(batch: &Batch, path: impl AsRef<Path>) -> AppResult<CommitmentStatement> {
    let actual = load_statement(path)?;
    let expected = statement_from_batch(batch);
    if actual != expected {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "public commitment statement does not match prover input",
        )
        .into());
    }
    println!("public commitment statement matched");
    Ok(actual)
}
