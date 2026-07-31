//! Stores normalized ZKMC obligation data.

use ark_mnt4_298::Fr;
use serde::Deserialize;

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ObligationKind {
    Init,
    Step,
    Fair,
}

impl ObligationKind {
    /// Returns the canonical field encoding.
    pub fn code(self) -> u64 {
        match self {
            Self::Init => 0,
            Self::Step => 1,
            Self::Fair => 2,
        }
    }
}

/// Stores private randomness for model hiding.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ModelBlinding {
    pub low: u64,
    pub high: u64,
}

#[derive(Clone, Debug)]
pub struct Obligation {
    pub kind: ObligationKind,
    pub label: String,
    pub secret_rows: usize,
    pub public_rows: usize,
    pub columns: usize,
    pub a_s: Vec<i64>,
    pub b_s: Vec<i64>,
    pub g_p: Vec<i64>,
    pub h_p: Vec<i64>,
    pub lambda: Vec<u64>,
    pub mu: Vec<u64>,
}

#[derive(Clone, Debug)]
pub struct Batch {
    pub benchmark: String,
    pub bound: u64,
    pub model_blinding: ModelBlinding,
    pub model_blinding_commitment: Fr,
    pub model_commitment: Fr,
    pub certificate_commitment: Fr,
    pub obligations: Vec<Obligation>,
}
