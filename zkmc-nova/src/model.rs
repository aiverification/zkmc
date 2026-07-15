//! Stores normalized ZKMC obligation data.

use serde::Deserialize;

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ObligationKind {
    Init,
    Step,
    Fair,
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
    pub model_tag: u64,
    pub certificate_tag: u64,
    pub bound: u64,
    pub obligations: Vec<Obligation>,
}
