// End-to-end prove + verify on one input file, with wall-clock timings.
use serde::Deserialize;
use serde_json::Value;
use std::{collections::HashMap, fs::File, time::Instant};
use zkmc_explicit::zkp;

#[derive(Debug, Deserialize)]
struct InputParams {
    embeddings: Embeddings,
    metadata: Metadata,
    #[serde(flatten)]
    _extra: HashMap<String, Value>,
}
#[derive(Debug, Deserialize)]
struct Embeddings {
    E_init: Vec<u64>,
    E_step: Vec<u64>,
    E_fairstep: Vec<u64>,
    E_S0: Vec<u64>,
    E_T: Vec<u64>,
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

fn main() {
    let path = std::env::args().nth(1).expect("usage: run_input <file.json>");
    let time_limit: u64 = std::env::args()
        .nth(2)
        .map(|s| s.parse().unwrap())
        .unwrap_or(2 * 60 * 60);
    let input: InputParams =
        serde_json::from_reader(File::open(&path).expect("open")).expect("parse");
    let e = &input.embeddings;
    let m = &input.metadata;
    println!(
        "{path}: states={} transitions={} |E_init|={} |E_step|={} |E_fairstep|={} |S0|={} |T|={}",
        m.num_states_enumerated,
        m.num_transitions_checked,
        e.E_init.len(),
        e.E_step.len(),
        e.E_fairstep.len(),
        e.E_S0.len(),
        e.E_T.len()
    );

    let t = Instant::now();
    let (proof, setup_ms) = zkp::prove(
        &e.E_init,
        &e.E_step,
        &e.E_fairstep,
        &e.E_S0,
        &e.E_T,
        m.num_states_enumerated,
        m.num_transitions_checked,
        &t,
        time_limit,
    );
    let prove_ms = t.elapsed().as_millis();
    let Some(proof) = proof else {
        println!("PROVER OOT after {prove_ms} ms (setup {setup_ms} ms)");
        return;
    };
    println!(
        "PROVER  total {} ms  (setup {} ms, rest {} ms)",
        prove_ms,
        setup_ms,
        prove_ms - setup_ms
    );

    let t = Instant::now();
    let ok = proof.verify(&e.E_init, &e.E_step, &e.E_fairstep, &t, time_limit);
    println!("VERIFIER {} ms -> {:?}", t.elapsed().as_millis(), ok);
}
