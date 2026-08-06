//! Tests committed obligation processing and public verification inputs.

use crate::{
    artifact::read_compressed,
    checker::check_plain,
    circuit::{circuit_satisfied, expected_final_state, initial_state, state_trace},
    commitment::compute_batch_commitments,
    decider::{repeated_compressed_proofs_differ, statement_states},
    input::parse_batch,
    model::Batch,
    runner::{fold_and_verify, refold_and_verify},
    statement::statement_from_batch,
};
use ark_bn254::{Fr, G1Projective};
use ark_ff::{PrimeField, Zero};
use ark_serialize::CanonicalSerialize;
use sonobe_primitives::commitments::{CommitmentOps, pedersen::Pedersen};
use std::{fs, time::SystemTime};

fn sample() -> Batch {
    parse_batch(include_str!("../examples/obligations.json")).unwrap()
}

#[test]
fn sample_plain_checks_hold() {
    let batch = sample();
    for obligation in &batch.obligations {
        assert!(check_plain(&batch, obligation).is_ok());
    }
}

#[test]
fn sample_circuits_hold() {
    let batch = sample();
    for (index, obligation) in batch.obligations.iter().enumerate() {
        assert!(circuit_satisfied(&batch, index, obligation).unwrap());
    }
}

#[test]
fn final_state_matches_commitments() {
    let batch = sample();
    let states = state_trace(&batch);
    assert_eq!(states.last().unwrap(), &expected_final_state(&batch));
}

#[test]
fn public_statement_reconstructs_recursive_states() {
    let batch = sample();
    let statement = statement_from_batch(&batch);
    let (initial, final_state) = statement_states(&statement).unwrap();
    assert_eq!(initial, initial_state(&batch));
    assert_eq!(final_state, expected_final_state(&batch));
}

#[test]
fn changed_public_statement_changes_expected_state() {
    let batch = sample();
    let mut statement = statement_from_batch(&batch);
    statement.bound += 1;
    let (initial, final_state) = statement_states(&statement).unwrap();
    assert_ne!(initial, initial_state(&batch));
    assert_ne!(final_state, expected_final_state(&batch));
}

#[test]
fn noncanonical_public_commitment_fails() {
    let batch = sample();
    let mut statement = statement_from_batch(&batch);
    statement.model_commitment = Fr::MODULUS.to_string();
    assert!(statement_states(&statement).is_err());
}

#[test]
fn compressed_reader_rejects_trailing_bytes() {
    let mut bytes = Vec::new();
    Fr::from(7_u64).serialize_compressed(&mut bytes).unwrap();
    bytes.push(0xaa);
    let nonce = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let path = std::env::temp_dir().join(format!("zkmc-trailing-{nonce}.bin"));
    fs::write(&path, bytes).unwrap();
    let result = read_compressed::<Fr>(&path);
    let _ = fs::remove_file(path);
    assert!(result.is_err());
}

#[test]
fn wrong_multiplier_fails() {
    let batch = sample();
    let mut obligation = batch.obligations[0].clone();
    obligation.lambda[0] += 1;
    assert!(check_plain(&batch, &obligation).is_err());
    assert!(!circuit_satisfied(&batch, 0, &obligation).unwrap());
}

#[test]
fn out_of_range_value_fails() {
    let batch = sample();
    let mut obligation = batch.obligations[0].clone();
    obligation.a_s[0] = batch.bound as i64 + 1;
    assert!(!circuit_satisfied(&batch, 0, &obligation).unwrap());
}

#[test]
fn model_mutation_changes_commitment() {
    let batch = sample();
    let mut obligations = batch.obligations.clone();
    obligations[0].a_s[0] += 1;
    let changed = compute_batch_commitments(&obligations, batch.bound, batch.model_blinding);
    assert_ne!(changed.model, batch.model_commitment);
    assert_eq!(changed.blinding, batch.model_blinding_commitment);
}

#[test]
fn certificate_mutation_changes_commitment() {
    let batch = sample();
    let mut obligations = batch.obligations.clone();
    obligations[0].g_p[0] += 1;
    let changed = compute_batch_commitments(&obligations, batch.bound, batch.model_blinding);
    assert_ne!(changed.certificate, batch.certificate_commitment);
    assert_eq!(changed.blinding, batch.model_blinding_commitment);
}

#[test]
fn obligation_order_changes_commitments() {
    let batch = sample();
    let mut obligations = batch.obligations.clone();
    obligations.swap(0, 1);
    let changed = compute_batch_commitments(&obligations, batch.bound, batch.model_blinding);
    assert_ne!(changed.model, batch.model_commitment);
    assert_ne!(changed.certificate, batch.certificate_commitment);
}

#[test]
fn model_blinding_changes_commitment() {
    let batch = sample();
    let mut blinding = batch.model_blinding;
    blinding.low ^= 1;
    let changed = compute_batch_commitments(&batch.obligations, batch.bound, blinding);
    assert_ne!(changed.blinding, batch.model_blinding_commitment);
    assert_ne!(changed.model, batch.model_commitment);
    assert_eq!(changed.certificate, batch.certificate_commitment);
}

#[test]
fn hiding_commitments_randomize_equal_messages() {
    type HidingPedersen = Pedersen<G1Projective, true>;

    let mut rng = ark_std::rand::rngs::OsRng;
    let key = HidingPedersen::generate_key(3, &mut rng).unwrap();
    let message = [Fr::from(3_u64), Fr::from(5_u64), Fr::from(8_u64)];
    let (first, first_randomness) = HidingPedersen::commit(&key, &message, &mut rng).unwrap();
    let (second, second_randomness) = HidingPedersen::commit(&key, &message, &mut rng).unwrap();

    assert_ne!(first_randomness, second_randomness);
    assert_ne!(first, second);
    HidingPedersen::open(&key, &message, &first_randomness, &first).unwrap();
    HidingPedersen::open(&key, &message, &second_randomness, &second).unwrap();
}

#[test]
#[ignore = "expensive Nova privacy integration"]
fn nova_pipeline_uses_hiding_randomness() {
    let batch = sample();
    let (nova_params, circuit, first) = fold_and_verify(&batch).unwrap();
    let second = refold_and_verify(&batch, &nova_params, &circuit).unwrap();

    assert_eq!(first.z_0, second.z_0);
    assert_eq!(first.z_i, second.z_i);
    assert!(!first.proof.0.r_e.is_zero());
    assert!(!first.proof.0.r_w.is_zero());
    assert!(!first.proof.2.r_w.is_zero());
    assert!(!first.proof.4.r_e.is_zero());
    assert!(!first.proof.4.r_w.is_zero());
    assert_ne!(first.proof.1.cm_e, second.proof.1.cm_e);
    assert_ne!(first.proof.1.cm_w, second.proof.1.cm_w);
    assert_ne!(first.proof.3.cm_w, second.proof.3.cm_w);
    assert_ne!(first.proof.5.cm_w, second.proof.5.cm_w);
    assert!(repeated_compressed_proofs_differ(nova_params, &circuit, &first).unwrap());
}
