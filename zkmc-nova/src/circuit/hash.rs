//! Updates ordered Poseidon digests inside R1CS.

use super::input::{SignedVar, StepInputVar};
use ark_crypto_primitives::sponge::{
    constraints::CryptographicSpongeVar,
    poseidon::{constraints::PoseidonSpongeVar, PoseidonConfig},
};
use ark_ff::PrimeField;
use ark_r1cs_std::fields::fp::FpVar;
use ark_relations::gr1cs::{ConstraintSystemRef, SynthesisError};

const MODEL_BLINDING_DOMAIN: u64 = 0x5a4b_4d00;
const MODEL_STEP_DOMAIN: u64 = 0x5a4b_4d02;
const CERTIFICATE_STEP_DOMAIN: u64 = 0x5a4b_4302;

/// Hashes the private model blinding limbs.
pub fn model_blinding_digest<F: PrimeField>(
    cs: ConstraintSystemRef<F>,
    config: &PoseidonConfig<F>,
    input: &StepInputVar<F>,
) -> Result<FpVar<F>, SynthesisError> {
    hash_fields(
        cs,
        config,
        &[
            FpVar::Constant(F::from(MODEL_BLINDING_DOMAIN)),
            input.model_blinding_low.clone(),
            input.model_blinding_high.clone(),
        ],
    )
}

/// Hashes one secret model slice into state.
pub fn update_model_digest<F: PrimeField>(
    cs: ConstraintSystemRef<F>,
    config: &PoseidonConfig<F>,
    previous: &FpVar<F>,
    input: &StepInputVar<F>,
) -> Result<FpVar<F>, SynthesisError> {
    let mut fields = vec![
        FpVar::Constant(F::from(MODEL_STEP_DOMAIN)),
        previous.clone(),
        input.index.clone(),
        input.model_blinding_low.clone(),
        input.model_blinding_high.clone(),
    ];
    append_signed(&mut fields, &input.a_s);
    append_signed(&mut fields, &input.b_s);
    hash_fields(cs, config, &fields)
}

/// Hashes one public statement slice into state.
pub fn update_certificate_digest<F: PrimeField>(
    cs: ConstraintSystemRef<F>,
    config: &PoseidonConfig<F>,
    previous: &FpVar<F>,
    input: &StepInputVar<F>,
) -> Result<FpVar<F>, SynthesisError> {
    let mut fields = vec![
        FpVar::Constant(F::from(CERTIFICATE_STEP_DOMAIN)),
        previous.clone(),
        input.index.clone(),
        input.kind.clone(),
    ];
    append_signed(&mut fields, &input.g_p);
    append_signed(&mut fields, &input.h_p);
    hash_fields(cs, config, &fields)
}

fn append_signed<F: PrimeField>(fields: &mut Vec<FpVar<F>>, values: &[SignedVar<F>]) {
    for value in values {
        let sign: FpVar<F> = value.negative.clone().into();
        fields.push(value.magnitude.clone());
        fields.push(sign);
    }
}

fn hash_fields<F: PrimeField>(
    cs: ConstraintSystemRef<F>,
    config: &PoseidonConfig<F>,
    fields: &[FpVar<F>],
) -> Result<FpVar<F>, SynthesisError> {
    let mut sponge = PoseidonSpongeVar::<F>::new(cs, config);
    sponge.absorb(&fields.to_vec())?;
    Ok(sponge.squeeze_field_elements(1)?.remove(0))
}
