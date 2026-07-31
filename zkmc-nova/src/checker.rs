//! Checks Farkas obligations outside circuits.

use crate::{
    commitment::{validate_batch_commitments, BatchCommitments},
    config::{MAX_COLUMNS, MAX_PUBLIC_ROWS, MAX_SECRET_ROWS},
    model::{Batch, Obligation},
    AppResult,
};
use std::io;

/// Verifies one obligation using integer arithmetic.
pub fn check_plain(batch: &Batch, obligation: &Obligation) -> Result<i128, String> {
    for column in 0..MAX_COLUMNS {
        let secret = (0..MAX_SECRET_ROWS)
            .map(|row| {
                obligation.a_s[row * MAX_COLUMNS + column] as i128 * obligation.lambda[row] as i128
            })
            .sum::<i128>();
        let public = (0..MAX_PUBLIC_ROWS)
            .map(|row| {
                obligation.g_p[row * MAX_COLUMNS + column] as i128 * obligation.mu[row] as i128
            })
            .sum::<i128>();
        if secret != -public {
            return Err(format!("column {column}: {secret} != {}", -public));
        }
    }

    let secret_scalar = (0..MAX_SECRET_ROWS)
        .map(|row| obligation.b_s[row] as i128 * obligation.lambda[row] as i128)
        .sum::<i128>();
    let public_scalar = (0..MAX_PUBLIC_ROWS)
        .map(|row| obligation.h_p[row] as i128 * obligation.mu[row] as i128)
        .sum::<i128>();
    let delta = -secret_scalar - public_scalar - 1;
    let bound = batch.bound as i128;

    if !(0..=bound).contains(&delta) {
        return Err(format!("delta {delta} lies outside [0, {bound}]"));
    }
    Ok(delta)
}

/// Checks every obligation and batch commitment.
pub fn run_plain(batch: &Batch) -> AppResult<()> {
    validate_batch_commitments(
        &batch.obligations,
        batch.bound,
        batch.model_blinding,
        BatchCommitments {
            blinding: batch.model_blinding_commitment,
            model: batch.model_commitment,
            certificate: batch.certificate_commitment,
        },
    )?;

    for obligation in &batch.obligations {
        let delta = check_plain(batch, obligation).map_err(|error| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!("{}: {error}", obligation.label),
            )
        })?;
        println!(
            "plain {:?}: {} | delta={delta}",
            obligation.kind, obligation.label
        );
    }
    println!(
        "plain checks passed: {} obligations",
        batch.obligations.len()
    );
    println!("batch Poseidon commitments verified");
    Ok(())
}
