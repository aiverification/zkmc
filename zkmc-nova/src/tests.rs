//! Tests Phase Two obligation processing.

use crate::{checker::check_plain, circuit::circuit_satisfied, input::parse_batch, model::Batch};

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
