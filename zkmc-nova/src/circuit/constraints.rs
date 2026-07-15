//! Enforces one padded Farkas obligation.

use super::input::{as_input, SignedVar, StepInput, StepInputVar};
use crate::{
    config::{COUNT_BITS, MAX_COLUMNS, MAX_PUBLIC_ROWS, MAX_SECRET_ROWS, RANGE_BITS},
    model::{Batch, Obligation},
};
use ark_bn254::Fr;
use ark_ff::PrimeField;
use ark_r1cs_std::{alloc::AllocVar, eq::EqGadget, fields::fp::FpVar};
use ark_relations::gr1cs::{ConstraintSystem, ConstraintSystemRef, SynthesisError};
use folding_schemes::{frontend::FCircuit, Error as NovaError};
use std::{cmp::Ordering, marker::PhantomData};

#[derive(Clone, Copy, Debug)]
pub struct ZkmcCircuit<F: PrimeField> {
    _field: PhantomData<F>,
}

impl<F: PrimeField> Default for ZkmcCircuit<F> {
    fn default() -> Self {
        Self {
            _field: PhantomData,
        }
    }
}

/// Builds the recursive public state vector.
pub fn initial_state(batch: &Batch, count: usize) -> Vec<Fr> {
    vec![
        Fr::from(count as u64),
        Fr::from(batch.obligations.len() as u64),
        Fr::from(batch.model_tag),
        Fr::from(batch.certificate_tag),
        Fr::from(batch.bound),
    ]
}

/// Checks one synthesized obligation circuit directly.
pub fn circuit_satisfied(
    batch: &Batch,
    index: usize,
    obligation: &Obligation,
) -> Result<bool, SynthesisError> {
    let cs = ConstraintSystem::<Fr>::new_ref();
    let state = initial_state(batch, index)
        .iter()
        .map(|value| FpVar::new_witness(cs.clone(), || Ok(*value)))
        .collect::<Result<Vec<_>, _>>()?;
    let input = StepInputVar::new_witness(cs.clone(), || Ok(as_input(batch, index, obligation)))?;
    let circuit = ZkmcCircuit::<Fr>::default();
    circuit.generate_step_constraints(cs.clone(), index, state, input)?;
    cs.is_satisfied()
}

impl<F: PrimeField> FCircuit<F> for ZkmcCircuit<F> {
    type Params = ();
    type ExternalInputs = StepInput<F>;
    type ExternalInputsVar = StepInputVar<F>;

    /// Creates the fixed padded step circuit.
    fn new(_params: Self::Params) -> Result<Self, NovaError> {
        Ok(Self::default())
    }

    /// Returns recursive state field element count.
    fn state_len(&self) -> usize {
        5
    }

    /// Enforces one Farkas certificate and transition.
    fn generate_step_constraints(
        &self,
        _cs: ConstraintSystemRef<F>,
        _i: usize,
        state: Vec<FpVar<F>>,
        input: Self::ExternalInputsVar,
    ) -> Result<Vec<FpVar<F>>, SynthesisError> {
        if state.len() != self.state_len() {
            return Err(SynthesisError::Unsatisfiable);
        }
        enforce_state_binding(&state, &input)?;
        enforce_input_ranges(&input)?;
        enforce_vector_equality(&input)?;
        enforce_contradiction_bound(&input)?;

        Ok(vec![
            state[0].clone() + F::one(),
            state[1].clone(),
            state[2].clone(),
            state[3].clone(),
            state[4].clone(),
        ])
    }
}

fn enforce_state_binding<F: PrimeField>(
    state: &[FpVar<F>],
    input: &StepInputVar<F>,
) -> Result<(), SynthesisError> {
    input.index.enforce_equal(&state[0])?;
    input.total.enforce_equal(&state[1])?;
    input.model_tag.enforce_equal(&state[2])?;
    input.certificate_tag.enforce_equal(&state[3])?;
    input.bound.enforce_equal(&state[4])?;
    enforce_bits(&input.index, COUNT_BITS)?;
    enforce_bits(&input.total, COUNT_BITS)?;
    input
        .index
        .enforce_cmp_unchecked(&input.total, Ordering::Less, false)?;
    Ok(())
}

fn enforce_input_ranges<F: PrimeField>(input: &StepInputVar<F>) -> Result<(), SynthesisError> {
    enforce_bits(&input.bound, RANGE_BITS)?;
    FpVar::Constant(F::zero()).enforce_cmp_unchecked(&input.bound, Ordering::Less, false)?;

    for value in input
        .a_s
        .iter()
        .chain(input.b_s.iter())
        .chain(input.g_p.iter())
        .chain(input.h_p.iter())
    {
        enforce_bounded(&value.magnitude, &input.bound)?;
    }
    for value in input.lambda.iter().chain(input.mu.iter()) {
        enforce_bounded(value, &input.bound)?;
    }
    Ok(())
}

fn enforce_vector_equality<F: PrimeField>(input: &StepInputVar<F>) -> Result<(), SynthesisError> {
    for column in 0..MAX_COLUMNS {
        let mut secret = FpVar::Constant(F::zero());
        let mut public = FpVar::Constant(F::zero());
        for row in 0..MAX_SECRET_ROWS {
            let coefficient = signed_value(&input.a_s[row * MAX_COLUMNS + column]);
            secret += coefficient * &input.lambda[row];
        }
        for row in 0..MAX_PUBLIC_ROWS {
            let coefficient = signed_value(&input.g_p[row * MAX_COLUMNS + column]);
            public += coefficient * &input.mu[row];
        }
        secret.enforce_equal(&(FpVar::Constant(F::zero()) - public))?;
    }
    Ok(())
}

fn enforce_contradiction_bound<F: PrimeField>(
    input: &StepInputVar<F>,
) -> Result<(), SynthesisError> {
    let mut secret = FpVar::Constant(F::zero());
    let mut public = FpVar::Constant(F::zero());
    for row in 0..MAX_SECRET_ROWS {
        secret += signed_value(&input.b_s[row]) * &input.lambda[row];
    }
    for row in 0..MAX_PUBLIC_ROWS {
        public += signed_value(&input.h_p[row]) * &input.mu[row];
    }
    let delta = FpVar::Constant(F::zero()) - secret - public - FpVar::Constant(F::one());
    enforce_bounded(&delta, &input.bound)
}

fn enforce_bits<F: PrimeField>(value: &FpVar<F>, bits: usize) -> Result<(), SynthesisError> {
    value.to_bits_le_with_top_bits_zero(bits)?;
    Ok(())
}

fn enforce_bounded<F: PrimeField>(
    value: &FpVar<F>,
    bound: &FpVar<F>,
) -> Result<(), SynthesisError> {
    enforce_bits(value, RANGE_BITS)?;
    value.enforce_cmp_unchecked(bound, Ordering::Less, true)
}

fn signed_value<F: PrimeField>(value: &SignedVar<F>) -> FpVar<F> {
    let sign: FpVar<F> = value.negative.clone().into();
    &value.magnitude - (&sign * &value.magnitude * F::from(2_u64))
}
