//! Computes binding commitments for obligation batches.

use crate::{
    AppResult,
    model::{ModelBlinding, Obligation},
};
use ark_bn254::Fr;
use ark_crypto_primitives::sponge::{
    CryptographicSponge,
    poseidon::{PoseidonConfig, PoseidonSponge},
};
use sonobe_primitives::transcripts::poseidon::poseidon_circom_config;
use std::io;

const MODEL_BLINDING_DOMAIN: u64 = 0x5a4b_4d00;
const MODEL_SEED_DOMAIN: u64 = 0x5a4b_4d01;
const MODEL_STEP_DOMAIN: u64 = 0x5a4b_4d02;
const CERTIFICATE_SEED_DOMAIN: u64 = 0x5a4b_4301;
const CERTIFICATE_STEP_DOMAIN: u64 = 0x5a4b_4302;

/// Stores both ordered batch commitments.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BatchCommitments {
    pub blinding: Fr,
    pub model: Fr,
    pub certificate: Fr,
}

/// Returns the canonical Poseidon configuration.
pub fn commitment_config() -> PoseidonConfig<Fr> {
    poseidon_circom_config()
}

/// Computes ordered model and certificate commitments.
pub fn compute_batch_commitments(
    obligations: &[Obligation],
    bound: u64,
    blinding: ModelBlinding,
) -> BatchCommitments {
    let config = commitment_config();
    let total = obligations.len();
    let mut model = model_seed_with_config(&config, total, bound);
    let mut certificate = certificate_seed_with_config(&config, total, bound);

    for (index, obligation) in obligations.iter().enumerate() {
        model = update_model_with_config(&config, model, index, obligation, blinding);
        certificate = update_certificate_with_config(&config, certificate, index, obligation);
    }

    BatchCommitments {
        blinding: model_blinding_commitment_with_config(&config, blinding),
        model,
        certificate,
    }
}

/// Recomputes and validates stored commitments.
pub fn validate_batch_commitments(
    obligations: &[Obligation],
    bound: u64,
    blinding: ModelBlinding,
    expected: BatchCommitments,
) -> AppResult<()> {
    let actual = compute_batch_commitments(obligations, bound, blinding);
    if actual != expected {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "batch commitments do not match obligation data",
        )
        .into());
    }
    Ok(())
}

/// Commits to the private model blinding value.
pub fn model_blinding_commitment(blinding: ModelBlinding) -> Fr {
    model_blinding_commitment_with_config(&commitment_config(), blinding)
}

pub fn model_blinding_commitment_with_config(
    config: &PoseidonConfig<Fr>,
    blinding: ModelBlinding,
) -> Fr {
    hash_fields(
        config,
        &[
            Fr::from(MODEL_BLINDING_DOMAIN),
            Fr::from(blinding.low),
            Fr::from(blinding.high),
        ],
    )
}

/// Returns the initial model digest.
pub fn model_seed(total: usize, bound: u64) -> Fr {
    model_seed_with_config(&commitment_config(), total, bound)
}

/// Returns the initial certificate digest.
pub fn certificate_seed(total: usize, bound: u64) -> Fr {
    certificate_seed_with_config(&commitment_config(), total, bound)
}

/// Updates the model digest with one obligation.
pub fn update_model(
    previous: Fr,
    index: usize,
    obligation: &Obligation,
    blinding: ModelBlinding,
) -> Fr {
    update_model_with_config(&commitment_config(), previous, index, obligation, blinding)
}

/// Updates the certificate digest with one obligation.
pub fn update_certificate(previous: Fr, index: usize, obligation: &Obligation) -> Fr {
    update_certificate_with_config(&commitment_config(), previous, index, obligation)
}

pub fn model_seed_with_config(config: &PoseidonConfig<Fr>, total: usize, bound: u64) -> Fr {
    hash_fields(
        config,
        &[
            Fr::from(MODEL_SEED_DOMAIN),
            Fr::from(total as u64),
            Fr::from(bound),
        ],
    )
}

pub fn certificate_seed_with_config(config: &PoseidonConfig<Fr>, total: usize, bound: u64) -> Fr {
    hash_fields(
        config,
        &[
            Fr::from(CERTIFICATE_SEED_DOMAIN),
            Fr::from(total as u64),
            Fr::from(bound),
        ],
    )
}

pub fn update_model_with_config(
    config: &PoseidonConfig<Fr>,
    previous: Fr,
    index: usize,
    obligation: &Obligation,
    blinding: ModelBlinding,
) -> Fr {
    let mut fields = vec![
        Fr::from(MODEL_STEP_DOMAIN),
        previous,
        Fr::from(index as u64),
        Fr::from(blinding.low),
        Fr::from(blinding.high),
    ];
    append_signed(&mut fields, &obligation.a_s);
    append_signed(&mut fields, &obligation.b_s);
    hash_fields(config, &fields)
}

pub fn update_certificate_with_config(
    config: &PoseidonConfig<Fr>,
    previous: Fr,
    index: usize,
    obligation: &Obligation,
) -> Fr {
    let mut fields = vec![
        Fr::from(CERTIFICATE_STEP_DOMAIN),
        previous,
        Fr::from(index as u64),
        Fr::from(obligation.kind.code()),
    ];
    append_signed(&mut fields, &obligation.g_p);
    append_signed(&mut fields, &obligation.h_p);
    hash_fields(config, &fields)
}

fn append_signed(fields: &mut Vec<Fr>, values: &[i64]) {
    for value in values {
        fields.push(Fr::from(value.unsigned_abs()));
        fields.push(Fr::from(if *value < 0 { 1_u64 } else { 0_u64 }));
    }
}

fn hash_fields(config: &PoseidonConfig<Fr>, fields: &[Fr]) -> Fr {
    let mut sponge = PoseidonSponge::<Fr>::new(config);
    sponge.absorb(&fields.to_vec());
    sponge.squeeze_field_elements(1)[0]
}
