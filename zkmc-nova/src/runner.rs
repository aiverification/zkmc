//! Runs plain, circuit, and Nova modes.

use crate::{
    checker::run_plain,
    circuit::{as_input, circuit_satisfied, initial_state, ZkmcCircuit},
    config::{MAX_COLUMNS, MAX_PUBLIC_ROWS, MAX_SECRET_ROWS},
    model::Batch,
    AppResult,
};
use ark_bn254::{Bn254, Fr, G1Projective as G1};
use ark_grumpkin::Projective as G2;
use folding_schemes::{
    commitment::{kzg::KZG, pedersen::Pedersen},
    folding::nova::{Nova, PreprocessorParam},
    frontend::FCircuit,
    transcript::poseidon::poseidon_canonical_config,
    FoldingScheme,
};
use std::{io, time::Instant};

/// Prints batch metadata and obligation shapes.
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

/// Checks every obligation circuit independently.
pub fn run_circuit(batch: &Batch) -> AppResult<()> {
    for (index, obligation) in batch.obligations.iter().enumerate() {
        if !circuit_satisfied(batch, index, obligation)? {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("circuit rejected {}", obligation.label),
            )
            .into());
        }
        println!("circuit step {index} satisfied");
    }
    println!(
        "circuit checks passed: {} obligations",
        batch.obligations.len()
    );
    Ok(())
}

/// Folds every obligation and verifies IVC.
pub fn run_nova(batch: &Batch) -> AppResult<()> {
    type Scheme = Nova<G1, G2, ZkmcCircuit<Fr>, KZG<'static, Bn254>, Pedersen<G2>, false>;

    run_plain(batch)?;
    let circuit = ZkmcCircuit::<Fr>::new(())?;
    let params = PreprocessorParam::new(poseidon_canonical_config::<Fr>(), circuit);
    let mut rng = ark_std::rand::rngs::OsRng;
    let nova_params = Scheme::preprocess(&mut rng, &params)?;
    let mut nova = Scheme::init(&nova_params, circuit, initial_state(batch, 0))?;

    for (index, obligation) in batch.obligations.iter().enumerate() {
        let start = Instant::now();
        nova.prove_step(&mut rng, as_input(batch, index, obligation), None)?;
        println!("nova step {index} folded in {:?}", start.elapsed());
    }

    Scheme::verify(nova_params.1.clone(), nova.ivc_proof())?;
    if nova.z_i != initial_state(batch, batch.obligations.len()) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "unexpected final recursive state",
        )
        .into());
    }
    println!("ivc verification passed");
    println!("final state: {:?}", nova.z_i);
    Ok(())
}
