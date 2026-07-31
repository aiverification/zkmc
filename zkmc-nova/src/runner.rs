//! Runs inspection, Nova folding, and decider modes with stable timings.

use crate::{
    circuit::{as_input, expected_final_state, initial_state, ZkmcCircuit},
    config::{MAX_COLUMNS, MAX_PUBLIC_ROWS, MAX_SECRET_ROWS},
    decider::{finalize_decider, NovaParams, NovaScheme},
    metrics::{print_duration, print_f64},
    model::Batch,
    statement::validate_statement,
    AppResult,
};
use ark_mnt4_298::Fr;
use folding_schemes::{
    folding::nova::PreprocessorParam, frontend::FCircuit,
    transcript::poseidon::poseidon_canonical_config, FoldingScheme,
};
use std::{io, path::Path, time::Instant};

/// Prints batch metadata and commitments.
pub fn inspect(batch: &Batch) {
    println!("benchmark: {}", batch.benchmark);
    println!(
        "obligations={}, bound={}, shape=({},{},{})",
        batch.obligations.len(),
        batch.bound,
        MAX_SECRET_ROWS,
        MAX_PUBLIC_ROWS,
        MAX_COLUMNS
    );
    println!(
        "model blinding commitment: {:?}",
        batch.model_blinding_commitment
    );
    println!("model commitment: {:?}", batch.model_commitment);
    println!("certificate commitment: {:?}", batch.certificate_commitment);
    for (index, obligation) in batch.obligations.iter().enumerate() {
        println!(
            "{index:03} {:?}: {} | rows=({},{}), columns={}",
            obligation.kind,
            obligation.label,
            obligation.secret_rows,
            obligation.public_rows,
            obligation.columns
        );
    }
}

/// Folds every obligation and verifies IVC.
pub fn run_nova(batch: &Batch) -> AppResult<()> {
    let _ = fold_and_verify(batch)?;
    Ok(())
}

/// Builds and stores the final decider proof.
pub fn run_decider(
    batch: &Batch,
    output_dir: impl AsRef<Path>,
    statement_path: impl AsRef<Path>,
) -> AppResult<()> {
    let statement = validate_statement(batch, statement_path)?;
    let (nova_params, circuit, nova) = fold_and_verify(batch)?;
    finalize_decider(batch, &statement, nova_params, circuit, nova, output_dir)
}

/// Executes folding and final proof generation.
pub fn run_all(
    batch: &Batch,
    output_dir: impl AsRef<Path>,
    statement_path: impl AsRef<Path>,
) -> AppResult<()> {
    inspect(batch);

    let statement = validate_statement(batch, statement_path)?;

    let (nova_params, circuit, nova) = fold_and_verify(batch)?;
    finalize_decider(batch, &statement, nova_params, circuit, nova, output_dir)
}

fn fold_and_verify(batch: &Batch) -> AppResult<(NovaParams, ZkmcCircuit<Fr>, NovaScheme)> {
    let setup_start = Instant::now();
    let poseidon_config = poseidon_canonical_config::<Fr>();
    let circuit = ZkmcCircuit::<Fr>::new(poseidon_config.clone())?;
    let params = PreprocessorParam::new(poseidon_config, circuit.clone());
    let mut rng = ark_std::rand::rngs::OsRng;
    let nova_params = NovaScheme::preprocess(&mut rng, &params)?;
    let mut nova = NovaScheme::init(&nova_params, circuit.clone(), initial_state(batch))?;
    print_duration("nova_setup_seconds", setup_start.elapsed());

    let fold_start = Instant::now();
    let mut step_seconds = Vec::with_capacity(batch.obligations.len());
    for (index, obligation) in batch.obligations.iter().enumerate() {
        let start = Instant::now();
        nova.prove_step(&mut rng, as_input(batch, index, obligation), None)?;
        let elapsed = start.elapsed();
        step_seconds.push(elapsed.as_secs_f64());
        println!("nova step {index} folded in {elapsed:?}");
    }
    print_duration("nova_fold_total_seconds", fold_start.elapsed());
    print_step_statistics(&step_seconds);

    let verify_start = Instant::now();
    NovaScheme::verify(nova_params.1.clone(), nova.ivc_proof())?;
    print_duration("nova_verify_seconds", verify_start.elapsed());
    if nova.z_i != expected_final_state(batch) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "unexpected final committed recursive state",
        )
        .into());
    }
    println!("ivc verification passed");
    println!("final committed state: {:?}", nova.z_i);
    Ok((nova_params, circuit, nova))
}

fn print_step_statistics(values: &[f64]) {
    if values.is_empty() {
        return;
    }
    let mut sorted = values.to_vec();
    sorted.sort_by(f64::total_cmp);
    let mean = sorted.iter().sum::<f64>() / sorted.len() as f64;
    let median = percentile(&sorted, 0.50);
    let p95 = percentile(&sorted, 0.95);
    let maximum = *sorted.last().unwrap_or(&0.0);
    print_f64("nova_step_mean_ms", mean * 1000.0);
    print_f64("nova_step_median_ms", median * 1000.0);
    print_f64("nova_step_p95_ms", p95 * 1000.0);
    print_f64("nova_step_max_ms", maximum * 1000.0);
}

fn percentile(sorted: &[f64], fraction: f64) -> f64 {
    let index = ((sorted.len() - 1) as f64 * fraction).ceil() as usize;
    sorted[index]
}
