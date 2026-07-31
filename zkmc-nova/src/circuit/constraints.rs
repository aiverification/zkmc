//! Enforces committed, ordered Farkas obligations.

use super::{
    hash::{model_blinding_digest, update_certificate_digest, update_model_digest},
    input::{as_input, SignedVar, StepInput, StepInputVar},
};
use crate::{
    commitment::{
        certificate_seed_with_config, commitment_config, model_seed_with_config,
        update_certificate_with_config, update_model_with_config,
    },
    config::{
        COUNT_BITS, MAX_COLUMNS, MAX_PUBLIC_ROWS, MAX_SECRET_ROWS, MODEL_BLINDING_BITS, RANGE_BITS,
    },
    model::{Batch, Obligation},
};
use ark_crypto_primitives::sponge::poseidon::PoseidonConfig;
use ark_ff::{One, PrimeField, Zero};
use ark_mnt4_298::Fr;
use ark_r1cs_std::{alloc::AllocVar, boolean::Boolean, eq::EqGadget, fields::fp::FpVar};
use ark_relations::gr1cs::{ConstraintSystem, ConstraintSystemRef, SynthesisError};
use folding_schemes::{
    frontend::FCircuit, transcript::poseidon::poseidon_canonical_config, Error as NovaError,
};
use std::{cmp::Ordering, marker::PhantomData};

#[derive(Clone, Debug)]
pub struct ZkmcCircuit<F: PrimeField> {
    poseidon_config: PoseidonConfig<F>,
    _field: PhantomData<F>,
}

impl<F: PrimeField> Default for ZkmcCircuit<F> {
    fn default() -> Self {
        Self {
            poseidon_config: poseidon_canonical_config::<F>(),
            _field: PhantomData,
        }
    }
}

/// Builds the initial committed recursive state.
pub fn initial_state(batch: &Batch) -> Vec<Fr> {
    let config = commitment_config();
    vec![
        Fr::zero(),
        Fr::from(batch.obligations.len() as u64),
        batch.model_commitment,
        batch.certificate_commitment,
        model_seed_with_config(&config, batch.obligations.len(), batch.bound),
        certificate_seed_with_config(&config, batch.obligations.len(), batch.bound),
        Fr::from(batch.bound),
        batch.model_blinding_commitment,
    ]
}

/// Builds every native state in execution order.
pub fn state_trace(batch: &Batch) -> Vec<Vec<Fr>> {
    let config = commitment_config();
    let mut current = initial_state(batch);
    let mut states = vec![current.clone()];

    for (index, obligation) in batch.obligations.iter().enumerate() {
        current[0] += Fr::one();
        current[4] =
            update_model_with_config(&config, current[4], index, obligation, batch.model_blinding);
        current[5] = update_certificate_with_config(&config, current[5], index, obligation);
        states.push(current.clone());
    }
    states
}

/// Returns the verifier-expected terminal state.
pub fn expected_final_state(batch: &Batch) -> Vec<Fr> {
    vec![
        Fr::from(batch.obligations.len() as u64),
        Fr::from(batch.obligations.len() as u64),
        batch.model_commitment,
        batch.certificate_commitment,
        batch.model_commitment,
        batch.certificate_commitment,
        Fr::from(batch.bound),
        batch.model_blinding_commitment,
    ]
}

/// Checks one synthesized obligation circuit directly.
pub fn circuit_satisfied(
    batch: &Batch,
    index: usize,
    obligation: &Obligation,
) -> Result<bool, SynthesisError> {
    let states = state_trace(batch);
    circuit_satisfied_with_state(batch, index, obligation, &states[index])
}

/// Checks one circuit from a supplied prior state.
pub fn circuit_satisfied_with_state(
    batch: &Batch,
    index: usize,
    obligation: &Obligation,
    state: &[Fr],
) -> Result<bool, SynthesisError> {
    let cs = ConstraintSystem::<Fr>::new_ref();
    let state_vars = state
        .iter()
        .map(|value| FpVar::new_witness(cs.clone(), || Ok(*value)))
        .collect::<Result<Vec<_>, _>>()?;
    let input = StepInputVar::new_witness(cs.clone(), || Ok(as_input(batch, index, obligation)))?;
    let circuit = ZkmcCircuit::<Fr>::default();
    circuit.generate_step_constraints(cs.clone(), index, state_vars, input)?;
    cs.is_satisfied()
}

impl<F: PrimeField> FCircuit<F> for ZkmcCircuit<F> {
    type Params = PoseidonConfig<F>;
    type ExternalInputs = StepInput<F>;
    type ExternalInputsVar = StepInputVar<F>;

    /// Creates the fixed committed step circuit.
    fn new(params: Self::Params) -> Result<Self, NovaError> {
        Ok(Self {
            poseidon_config: params,
            _field: PhantomData,
        })
    }

    /// Returns recursive state field element count.
    fn state_len(&self) -> usize {
        8
    }

    /// Enforces one committed Farkas transition.
    fn generate_step_constraints(
        &self,
        cs: ConstraintSystemRef<F>,
        _i: usize,
        state: Vec<FpVar<F>>,
        input: Self::ExternalInputsVar,
    ) -> Result<Vec<FpVar<F>>, SynthesisError> {
        if state.len() != self.state_len() {
            return Err(SynthesisError::Unsatisfiable);
        }
        enforce_state_binding(&state, &input)?;
        enforce_input_ranges(&input)?;
        model_blinding_digest(cs.clone(), &self.poseidon_config, &input)?
            .enforce_equal(&state[7])?;
        enforce_vector_equality(&input)?;
        enforce_contradiction_bound(&input)?;

        let model_digest =
            update_model_digest(cs.clone(), &self.poseidon_config, &state[4], &input)?;
        let certificate_digest =
            update_certificate_digest(cs, &self.poseidon_config, &state[5], &input)?;

        Ok(vec![
            state[0].clone() + F::one(),
            state[1].clone(),
            state[2].clone(),
            state[3].clone(),
            model_digest,
            certificate_digest,
            state[6].clone(),
            state[7].clone(),
        ])
    }
}

fn enforce_state_binding<F: PrimeField>(
    state: &[FpVar<F>],
    input: &StepInputVar<F>,
) -> Result<(), SynthesisError> {
    input.index.enforce_equal(&state[0])?;
    input.total.enforce_equal(&state[1])?;
    input.bound.enforce_equal(&state[6])?;
    enforce_bits(&input.index, COUNT_BITS)?;
    enforce_bits(&input.total, COUNT_BITS)?;
    input
        .index
        .enforce_cmp_unchecked(&input.total, Ordering::Less, false)?;
    Ok(())
}

fn enforce_input_ranges<F: PrimeField>(input: &StepInputVar<F>) -> Result<(), SynthesisError> {
    enforce_bits(&input.bound, RANGE_BITS)?;
    enforce_model_blinding(input)?;
    FpVar::Constant(F::zero()).enforce_cmp_unchecked(&input.bound, Ordering::Less, false)?;
    enforce_kind(&input.kind)?;

    for value in input
        .a_s
        .iter()
        .chain(input.b_s.iter())
        .chain(input.g_p.iter())
        .chain(input.h_p.iter())
    {
        enforce_bounded(&value.magnitude, &input.bound)?;
        enforce_canonical_sign(value)?;
    }
    for value in input.lambda.iter().chain(input.mu.iter()) {
        enforce_bounded(value, &input.bound)?;
    }
    Ok(())
}

fn enforce_model_blinding<F: PrimeField>(input: &StepInputVar<F>) -> Result<(), SynthesisError> {
    enforce_bits(&input.model_blinding_low, MODEL_BLINDING_BITS)?;
    enforce_bits(&input.model_blinding_high, MODEL_BLINDING_BITS)?;
    let low_zero = input
        .model_blinding_low
        .is_eq(&FpVar::Constant(F::zero()))?;
    let high_zero = input
        .model_blinding_high
        .is_eq(&FpVar::Constant(F::zero()))?;
    Boolean::enforce_kary_nand(&[low_zero, high_zero])
}

fn enforce_kind<F: PrimeField>(kind: &FpVar<F>) -> Result<(), SynthesisError> {
    let one = FpVar::Constant(F::one());
    let two = FpVar::Constant(F::from(2_u64));
    let polynomial = kind.clone() * (kind.clone() - one) * (kind.clone() - two);
    polynomial.enforce_equal(&FpVar::Constant(F::zero()))
}

fn enforce_canonical_sign<F: PrimeField>(value: &SignedVar<F>) -> Result<(), SynthesisError> {
    let is_zero = value.magnitude.is_eq(&FpVar::Constant(F::zero()))?;
    Boolean::enforce_kary_nand(&[value.negative.clone(), is_zero])
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
    let _ = value.to_bits_le_with_top_bits_zero(bits)?;
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
