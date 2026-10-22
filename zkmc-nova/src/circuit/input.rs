//! Allocates padded obligation circuit inputs.

use crate::{
    config::{MAX_COLUMNS, MAX_PUBLIC_ROWS, MAX_SECRET_ROWS},
    model::{Batch, Obligation},
};
use ark_bn254::Fr;
use ark_ff::PrimeField;
use ark_r1cs_std::{
    alloc::{AllocVar, AllocationMode},
    boolean::Boolean,
    fields::fp::FpVar,
};
use ark_relations::gr1cs::{ConstraintSystemRef, Namespace, SynthesisError};
use std::borrow::Borrow;

#[derive(Clone, Debug)]
pub struct SignedInput<F: PrimeField> {
    pub magnitude: F,
    pub negative: bool,
}

#[derive(Clone, Debug)]
pub struct SignedVar<F: PrimeField> {
    pub magnitude: FpVar<F>,
    pub negative: Boolean<F>,
}

#[derive(Clone, Debug)]
pub struct StepInput<F: PrimeField> {
    pub index: F,
    pub total: F,
    pub kind: F,
    pub bound: F,
    pub model_blinding_low: F,
    pub model_blinding_high: F,
    pub a_s: Vec<SignedInput<F>>,
    pub b_s: Vec<SignedInput<F>>,
    pub g_p: Vec<SignedInput<F>>,
    pub h_p: Vec<SignedInput<F>>,
    pub lambda: Vec<F>,
    pub mu: Vec<F>,
}

#[derive(Clone, Debug)]
pub struct StepInputVar<F: PrimeField> {
    pub index: FpVar<F>,
    pub total: FpVar<F>,
    pub kind: FpVar<F>,
    pub bound: FpVar<F>,
    pub model_blinding_low: FpVar<F>,
    pub model_blinding_high: FpVar<F>,
    pub a_s: Vec<SignedVar<F>>,
    pub b_s: Vec<SignedVar<F>>,
    pub g_p: Vec<SignedVar<F>>,
    pub h_p: Vec<SignedVar<F>>,
    pub lambda: Vec<FpVar<F>>,
    pub mu: Vec<FpVar<F>>,
}

/// Converts one obligation into field inputs.
pub fn as_input(batch: &Batch, index: usize, obligation: &Obligation) -> StepInput<Fr> {
    StepInput {
        index: Fr::from(index as u64),
        total: Fr::from(batch.obligations.len() as u64),
        kind: Fr::from(obligation.kind.code()),
        bound: Fr::from(batch.bound),
        model_blinding_low: Fr::from(batch.model_blinding.low),
        model_blinding_high: Fr::from(batch.model_blinding.high),
        a_s: obligation.a_s.iter().copied().map(signed_input).collect(),
        b_s: obligation.b_s.iter().copied().map(signed_input).collect(),
        g_p: obligation.g_p.iter().copied().map(signed_input).collect(),
        h_p: obligation.h_p.iter().copied().map(signed_input).collect(),
        lambda: obligation.lambda.iter().copied().map(Fr::from).collect(),
        mu: obligation.mu.iter().copied().map(Fr::from).collect(),
    }
}

fn signed_input<F: PrimeField>(value: i64) -> SignedInput<F> {
    SignedInput {
        magnitude: F::from(value.unsigned_abs()),
        negative: value < 0,
    }
}

fn zero_signed<F: PrimeField>(size: usize) -> Vec<SignedInput<F>> {
    vec![
        SignedInput {
            magnitude: F::zero(),
            negative: false,
        };
        size
    ]
}

impl<F: PrimeField> Default for StepInput<F> {
    fn default() -> Self {
        Self {
            index: F::zero(),
            total: F::one(),
            kind: F::zero(),
            bound: F::one(),
            model_blinding_low: F::one(),
            model_blinding_high: F::zero(),
            a_s: zero_signed(MAX_SECRET_ROWS * MAX_COLUMNS),
            b_s: zero_signed(MAX_SECRET_ROWS),
            g_p: zero_signed(MAX_PUBLIC_ROWS * MAX_COLUMNS),
            h_p: zero_signed(MAX_PUBLIC_ROWS),
            lambda: vec![F::zero(); MAX_SECRET_ROWS],
            mu: vec![F::zero(); MAX_PUBLIC_ROWS],
        }
    }
}

impl<F: PrimeField> Default for StepInputVar<F> {
    fn default() -> Self {
        let zero = FpVar::Constant(F::zero());
        let signed = |size| {
            (0..size)
                .map(|_| SignedVar {
                    magnitude: zero.clone(),
                    negative: Boolean::FALSE,
                })
                .collect()
        };
        Self {
            index: zero.clone(),
            total: FpVar::Constant(F::one()),
            kind: zero.clone(),
            bound: FpVar::Constant(F::one()),
            model_blinding_low: FpVar::Constant(F::one()),
            model_blinding_high: zero.clone(),
            a_s: signed(MAX_SECRET_ROWS * MAX_COLUMNS),
            b_s: signed(MAX_SECRET_ROWS),
            g_p: signed(MAX_PUBLIC_ROWS * MAX_COLUMNS),
            h_p: signed(MAX_PUBLIC_ROWS),
            lambda: vec![zero.clone(); MAX_SECRET_ROWS],
            mu: vec![zero; MAX_PUBLIC_ROWS],
        }
    }
}

impl<F: PrimeField> AllocVar<StepInput<F>, F> for StepInputVar<F> {
    /// Allocates one complete padded obligation input.
    fn new_variable<T: Borrow<StepInput<F>>>(
        cs: impl Into<Namespace<F>>,
        value: impl FnOnce() -> Result<T, SynthesisError>,
        mode: AllocationMode,
    ) -> Result<Self, SynthesisError> {
        let cs = cs.into().cs();
        let input = value()?;
        let input = input.borrow();
        Ok(Self {
            index: FpVar::new_variable(cs.clone(), || Ok(input.index), mode)?,
            total: FpVar::new_variable(cs.clone(), || Ok(input.total), mode)?,
            kind: FpVar::new_variable(cs.clone(), || Ok(input.kind), mode)?,
            bound: FpVar::new_variable(cs.clone(), || Ok(input.bound), mode)?,
            model_blinding_low: FpVar::new_variable(
                cs.clone(),
                || Ok(input.model_blinding_low),
                mode,
            )?,
            model_blinding_high: FpVar::new_variable(
                cs.clone(),
                || Ok(input.model_blinding_high),
                mode,
            )?,
            a_s: alloc_signed(cs.clone(), &input.a_s, mode)?,
            b_s: alloc_signed(cs.clone(), &input.b_s, mode)?,
            g_p: alloc_signed(cs.clone(), &input.g_p, mode)?,
            h_p: alloc_signed(cs.clone(), &input.h_p, mode)?,
            lambda: alloc_fields(cs.clone(), &input.lambda, mode)?,
            mu: alloc_fields(cs, &input.mu, mode)?,
        })
    }
}

fn alloc_fields<F: PrimeField>(
    cs: ConstraintSystemRef<F>,
    values: &[F],
    mode: AllocationMode,
) -> Result<Vec<FpVar<F>>, SynthesisError> {
    values
        .iter()
        .map(|value| FpVar::new_variable(cs.clone(), || Ok(*value), mode))
        .collect()
}

fn alloc_signed<F: PrimeField>(
    cs: ConstraintSystemRef<F>,
    values: &[SignedInput<F>],
    mode: AllocationMode,
) -> Result<Vec<SignedVar<F>>, SynthesisError> {
    values
        .iter()
        .map(|value| {
            Ok(SignedVar {
                magnitude: FpVar::new_variable(cs.clone(), || Ok(value.magnitude), mode)?,
                negative: Boolean::new_variable(cs.clone(), || Ok(value.negative), mode)?,
            })
        })
        .collect()
}
