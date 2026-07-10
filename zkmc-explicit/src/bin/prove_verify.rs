use ark_bls12_381::Bls12_381;
use ark_ec::pairing::Pairing;
use serde::Deserialize;
use serde_json::Value;
use std::collections::HashMap;
use std::env;
use std::fs::File;
use std::process;
use std::time::Instant;
use zkmc_explicit::zkp;

pub type F = <Bls12_381 as Pairing>::ScalarField;

#[derive(Debug, Deserialize)]
struct InputParams {
    embeddings: Embeddings,
    metadata: Metadata,
    verification: Option<Verification>,
    #[serde(flatten)]
    _extra: HashMap<String, Value>,
}

#[derive(Debug, Deserialize)]
struct Embeddings {
    #[serde(rename = "E_init")]
    e_init: Vec<u64>,
    #[serde(rename = "E_step")]
    e_step: Vec<u64>,
    #[serde(rename = "E_fairstep")]
    e_fairstep: Vec<u64>,
    #[serde(rename = "E_S0")]
    e_s0: Vec<u64>,
    #[serde(rename = "E_T")]
    e_t: Vec<u64>,
    #[serde(flatten)]
    _extra: HashMap<String, Value>,
}

#[derive(Debug, Deserialize)]
struct Metadata {
    num_states_enumerated: usize,
    num_transitions_checked: usize,
    #[serde(flatten)]
    _extra: HashMap<String, Value>,
}

#[derive(Debug, Deserialize)]
struct Verification {
    all_disjoint: bool,
    init_intersection_size: usize,
    step_intersection_size: usize,
    fairstep_intersection_size: usize,
    #[serde(flatten)]
    _extra: HashMap<String, Value>,
}

fn fail(message: &str, code: i32) -> ! {
    eprintln!("{message}");
    process::exit(code);
}

fn check_embedding_bounds(input: &InputParams) {
    if input.metadata.num_states_enumerated == 0 {
        fail("invalid input: no enumerated states", 2);
    }
    if input.metadata.num_transitions_checked == 0 {
        fail("invalid input: no checked transitions", 2);
    }

    for state in &input.embeddings.e_s0 {
        if *state >= input.metadata.num_states_enumerated as u64 {
            fail("invalid input: E_S0 contains an out-of-range state", 2);
        }
    }
    for state in &input.embeddings.e_init {
        if *state >= input.metadata.num_states_enumerated as u64 {
            fail("invalid input: E_init contains an out-of-range state", 2);
        }
    }
    for transition in input
        .embeddings
        .e_t
        .iter()
        .chain(input.embeddings.e_step.iter())
        .chain(input.embeddings.e_fairstep.iter())
    {
        if *transition >= input.metadata.num_transitions_checked as u64 {
            fail("invalid input: transition embedding is out of range", 2);
        }
    }
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() != 2 {
        fail("usage: prove_verify <zkexplicit-json>", 2);
    }

    let input_path = &args[1];
    let file = File::open(input_path)
        .unwrap_or_else(|err| fail(&format!("could not open input file {input_path}: {err}"), 2));
    let input: InputParams = serde_json::from_reader(file).unwrap_or_else(|err| {
        fail(
            &format!("could not parse input JSON {input_path}: {err}"),
            2,
        )
    });

    check_embedding_bounds(&input);

    println!("input: {input_path}");
    println!(
        "states: {}, transitions: {}",
        input.metadata.num_states_enumerated, input.metadata.num_transitions_checked
    );
    println!(
        "bad sets: E_init={}, E_step={}, E_fairstep={}",
        input.embeddings.e_init.len(),
        input.embeddings.e_step.len(),
        input.embeddings.e_fairstep.len()
    );
    if let Some(verification) = &input.verification {
        println!(
            "encoder_all_disjoint: {} (init={}, step={}, fair={})",
            verification.all_disjoint,
            verification.init_intersection_size,
            verification.step_intersection_size,
            verification.fairstep_intersection_size
        );
    }

    let prove_timer = Instant::now();
    let prove_time_limit = 2 * 60 * 60;
    let (proof_opt, setup_time) = zkp::prove(
        &input.embeddings.e_init,
        &input.embeddings.e_step,
        &input.embeddings.e_fairstep,
        &input.embeddings.e_s0,
        &input.embeddings.e_t,
        input.metadata.num_states_enumerated,
        input.metadata.num_transitions_checked,
        &prove_timer,
        prove_time_limit,
    );

    let Some(proof) = proof_opt else {
        fail(
            &format!("proof generation timed out after setup {setup_time} ms"),
            3,
        );
    };

    let prove_ms = prove_timer.elapsed().as_millis();
    let prove_without_setup_ms = prove_ms.saturating_sub(setup_time);
    println!("setup_ms: {setup_time}");
    println!("prove_ms_without_setup: {prove_without_setup_ms}");

    let verify_timer = Instant::now();
    let verify_time_limit = 2 * 60 * 60;
    let verified_opt = proof.verify(
        &input.embeddings.e_init,
        &input.embeddings.e_step,
        &input.embeddings.e_fairstep,
        &verify_timer,
        verify_time_limit,
    );

    let Some(verified) = verified_opt else {
        fail("verification timed out", 4);
    };

    let verify_ms = verify_timer.elapsed().as_millis();
    println!("verify_ms: {verify_ms}");
    println!("proof_verified: {verified}");

    if !verified {
        process::exit(5);
    }
}
