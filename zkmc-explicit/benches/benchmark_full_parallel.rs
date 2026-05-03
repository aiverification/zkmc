use criterion::{criterion_group, criterion_main, Criterion};
use zkmc_explicit::zkp;

use std::{fs, fs::File, path::Path, io::Write, time::Instant};
use serde_json::Value;
use serde::Deserialize;
use ark_bls12_381::Bls12_381 as bls;
use ark_ec::pairing::Pairing;
pub type F = <bls as Pairing>::ScalarField;
use ark_std::collections::HashMap;

#[derive(Debug, Deserialize)]
pub struct InputParams{
    embeddings: Embeddings,
    metadata: Metadata,
    // Anything else we don't care about
    #[serde(flatten)]
    extra: HashMap<String, Value>,
}

#[derive(Debug, Deserialize)]
pub struct Embeddings{
    E_init: Vec<u64>,
    E_step: Vec<u64>,
    E_fairstep: Vec<u64>,
    E_S0: Vec<u64>,
    E_T: Vec<u64>,
    #[serde(flatten)]
    extra: HashMap<String, Value>,
}

#[derive(Debug, Deserialize)]
pub struct Metadata{
    num_states_enumerated: usize,
    num_transitions_checked: usize,
    #[serde(flatten)]
    extra: HashMap<String, Value>,
}

fn prove_and_verify_benchmark(c: &mut Criterion) {
    let sample_size: usize = 1;
    let path_to_file_g = "input/".to_string();
    let candidate_files = vec![
        "dhcp_i1a2.json".to_string(),
        "dhcp_i2a2.json".to_string(),
        "dhcp_i4a2.json".to_string(),
        "rr_2.json".to_string(),
        "rr_3.json".to_string(),
    ];

    for input_file in candidate_files{
        let log_string = "output/bench_".to_string() + &input_file.clone() + ".log";
        let path = Path::new(&log_string);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let mut log_file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path).unwrap();

        let input_str = path_to_file_g.clone() + &input_file.clone();

        for sample in 0..sample_size{
            // Get data from input file
            println!("================ Reading data from file ================");
            let file = File::open(input_str.clone()).expect("Error opening file ");
            let input: InputParams = serde_json::from_reader(file).expect("Failed to parse JSON ");
            assert!(input.metadata.num_states_enumerated != 0, "Must have states!");
            for state in input.embeddings.E_S0.iter(){
                assert!(*state < input.metadata.num_states_enumerated as u64, "S_0 must be in valid states!");
            }
            for e in input.embeddings.E_init.iter(){
                assert!(*e < input.metadata.num_states_enumerated as u64, "e in E_init must be valid states!");
            }

            assert!(input.metadata.num_transitions_checked != 0, "Must have states!");
            for state in input.embeddings.E_init.iter(){
                assert!(*state < input.metadata.num_states_enumerated as u64, "T must be in valid transitions!");
            }
            for e in input.embeddings.E_step.iter(){
                assert!(*e < input.metadata.num_transitions_checked as u64, "e in E_step must be valid transition!");
            }
            for e in input.embeddings.E_fairstep.iter(){
                assert!(*e < input.metadata.num_transitions_checked as u64, "e in E_fairstep must be valid transition!");
            }

            let prove_timer = Instant::now();
            let prove_time_limit: u64 = 2 * 60 * 60; //2h in seconds
            let (proof_opt, setup_time) = zkp::prove(&input.embeddings.E_init, &input.embeddings.E_step, &input.embeddings.E_fairstep, &input.embeddings.E_S0, &input.embeddings.E_T, input.metadata.num_states_enumerated, input.metadata.num_transitions_checked, &prove_timer, prove_time_limit);
            if proof_opt.is_none(){
                // In this case, timer exceeded -> write this to file
                writeln!(log_file, "Sample no. {:?} -- Setup: {:?}ms -- Prover OOT.", sample, setup_time).expect("Error writing to log file");
                break; // Include if you want to exit after first OOT attempt across multiple samples
            }
            else{
                let proof = proof_opt.unwrap();
                let prove_time = prove_timer.elapsed().as_millis();
                let prove_time_min_setup = prove_time - setup_time;
                let verify_timer = Instant::now();
                let verify_time_limit: u64 = 2 * 60 * 60; // 2h in seconds
                let verified_opt = proof.verify(&input.embeddings.E_init, &input.embeddings.E_step, &input.embeddings.E_fairstep, &verify_timer, verify_time_limit);
                if verified_opt.is_none(){
                    writeln!(log_file, "Sample no. {:?} -- Setup: {:?}ms -- Prover: {:?}ms -- Verifier OOT.", sample, setup_time, prove_time_min_setup).expect("Error writing to log file");
                }
                else {
                    let verified = verified_opt.unwrap();
                    let verified_time = verify_timer.elapsed().as_millis();
                    if verified{
                        writeln!(log_file, "Sample no. {:?} -- Setup: {:?}ms -- Prover: {:?}ms -- Verifier: {:?}ms.", sample, setup_time, prove_time_min_setup, verified_time).expect("Error writing to log file");
                    }
                }
            }
        }
    }    
}

criterion_group!(benches, prove_and_verify_benchmark);
criterion_main!(benches);