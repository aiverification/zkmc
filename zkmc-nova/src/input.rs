//! Loads and validates obligation batches.

use crate::{
    config::{max_bound, MAX_COLUMNS, MAX_PUBLIC_ROWS, MAX_SECRET_ROWS},
    model::{Batch, Obligation, ObligationKind},
    AppResult,
};
use serde::Deserialize;
use std::{fs, io, path::Path};

#[derive(Clone, Debug, Deserialize)]
struct RawBatch {
    schema_version: u32,
    benchmark: String,
    model_tag: u64,
    certificate_tag: u64,
    bound: u64,
    obligations: Vec<RawObligation>,
}

#[derive(Clone, Debug, Deserialize)]
struct RawObligation {
    kind: ObligationKind,
    label: String,
    a_s: Vec<Vec<i64>>,
    b_s: Vec<i64>,
    g_p: Vec<Vec<i64>>,
    h_p: Vec<i64>,
    lambda: Option<Vec<u64>>,
    mu: Option<Vec<u64>>,
}

/// Loads and validates one JSON batch.
pub fn load_batch(path: impl AsRef<Path>) -> AppResult<Batch> {
    parse_batch(&fs::read_to_string(path)?)
}

/// Parses and validates one JSON batch.
pub fn parse_batch(text: &str) -> AppResult<Batch> {
    let raw: RawBatch = serde_json::from_str(text)?;
    prepare(raw).map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error).into())
}

fn prepare(raw: RawBatch) -> Result<Batch, String> {
    validate_batch_header(&raw)?;
    let obligations = raw
        .obligations
        .into_iter()
        .map(|item| prepare_obligation(item, raw.bound))
        .collect::<Result<Vec<_>, _>>()?;

    Ok(Batch {
        benchmark: raw.benchmark,
        model_tag: raw.model_tag,
        certificate_tag: raw.certificate_tag,
        bound: raw.bound,
        obligations,
    })
}

fn validate_batch_header(raw: &RawBatch) -> Result<(), String> {
    if raw.schema_version != 1 {
        return Err("schema_version must equal 1".to_string());
    }
    if raw.obligations.is_empty() {
        return Err("at least one obligation is required".to_string());
    }
    if raw.obligations.len() > max_bound() as usize {
        return Err("too many obligations for count range".to_string());
    }
    if raw.bound == 0 || raw.bound > max_bound() {
        return Err(format!("bound must lie in [1, {}]", max_bound()));
    }
    Ok(())
}

fn prepare_obligation(item: RawObligation, bound: u64) -> Result<Obligation, String> {
    let secret_rows = item.a_s.len();
    let public_rows = item.g_p.len();
    let a_columns = matrix_columns("a_s", &item.a_s)?;
    let g_columns = matrix_columns("g_p", &item.g_p)?;

    validate_dimensions(&item, secret_rows, public_rows, a_columns, g_columns)?;
    validate_coefficients(&item, bound)?;

    let lambda = item
        .lambda
        .ok_or_else(|| format!("{} is missing lambda", item.label))?;
    let mu = item
        .mu
        .ok_or_else(|| format!("{} is missing mu", item.label))?;

    if lambda.len() != secret_rows || mu.len() != public_rows {
        return Err(format!("{} witness dimensions mismatch", item.label));
    }
    if lambda.iter().chain(mu.iter()).any(|value| *value > bound) {
        return Err(format!("{} multiplier exceeds bound", item.label));
    }

    Ok(Obligation {
        kind: item.kind,
        label: item.label,
        secret_rows,
        public_rows,
        columns: a_columns,
        a_s: pad_matrix(&item.a_s, MAX_SECRET_ROWS),
        b_s: pad_i64(&item.b_s, MAX_SECRET_ROWS),
        g_p: pad_matrix(&item.g_p, MAX_PUBLIC_ROWS),
        h_p: pad_i64(&item.h_p, MAX_PUBLIC_ROWS),
        lambda: pad_u64(&lambda, MAX_SECRET_ROWS),
        mu: pad_u64(&mu, MAX_PUBLIC_ROWS),
    })
}

fn validate_dimensions(
    item: &RawObligation,
    secret_rows: usize,
    public_rows: usize,
    a_columns: usize,
    g_columns: usize,
) -> Result<(), String> {
    if secret_rows > MAX_SECRET_ROWS || public_rows > MAX_PUBLIC_ROWS {
        return Err(format!("{} exceeds configured row limits", item.label));
    }
    if a_columns != g_columns || a_columns > MAX_COLUMNS {
        return Err(format!("{} has incompatible columns", item.label));
    }
    if item.b_s.len() != secret_rows || item.h_p.len() != public_rows {
        return Err(format!("{} vector dimensions mismatch", item.label));
    }
    Ok(())
}

fn validate_coefficients(item: &RawObligation, bound: u64) -> Result<(), String> {
    for value in item
        .a_s
        .iter()
        .flatten()
        .chain(item.b_s.iter())
        .chain(item.g_p.iter().flatten())
        .chain(item.h_p.iter())
    {
        if checked_abs(*value)? > bound {
            return Err(format!("{} coefficient exceeds bound", item.label));
        }
    }
    Ok(())
}

fn checked_abs(value: i64) -> Result<u64, String> {
    value
        .checked_abs()
        .map(|magnitude| magnitude as u64)
        .ok_or_else(|| "i64::MIN is unsupported".to_string())
}

fn matrix_columns(name: &str, matrix: &[Vec<i64>]) -> Result<usize, String> {
    let columns = matrix
        .first()
        .ok_or_else(|| format!("{name} must have one row"))?
        .len();
    if columns == 0 {
        return Err(format!("{name} must have one column"));
    }
    if matrix.iter().any(|row| row.len() != columns) {
        return Err(format!("{name} rows must be rectangular"));
    }
    Ok(columns)
}

fn pad_matrix(matrix: &[Vec<i64>], rows: usize) -> Vec<i64> {
    let mut padded = vec![0; rows * MAX_COLUMNS];
    for (row_index, row) in matrix.iter().enumerate() {
        for (column_index, value) in row.iter().enumerate() {
            padded[row_index * MAX_COLUMNS + column_index] = *value;
        }
    }
    padded
}

fn pad_i64(values: &[i64], size: usize) -> Vec<i64> {
    let mut padded = vec![0; size];
    padded[..values.len()].copy_from_slice(values);
    padded
}

fn pad_u64(values: &[u64], size: usize) -> Vec<u64> {
    let mut padded = vec![0; size];
    padded[..values.len()].copy_from_slice(values);
    padded
}
