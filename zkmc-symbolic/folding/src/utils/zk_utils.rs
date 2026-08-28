use zkmatrix::{
    mat::Mat,
};
use zkmatrix::utils::curve::{ZpElement, ConvertToZp, GtElement};
use std::collections::HashMap;

pub fn mat_to_zp<T: ConvertToZp>(mat: &Mat<T>) -> Mat<ZpElement> {
    let data = mat.data.iter()
        .map(|(r, c, v)| (*r, *c, v.to_zp()))
        .collect();
    Mat::new_from_data_vec(&mat.id, mat.shape, data)
}

pub fn mat_scalar_mul(mat: &Mat<ZpElement>, r: ZpElement) -> Mat<ZpElement> {
    let data = mat.data.iter()
        .map(|(row, col, v)| (*row, *col, *v * r))
        .collect();
    Mat::new_from_data_vec(&mat.id, mat.shape, data)
}

pub fn mat_addition(mat_a: &Mat<ZpElement>, mat_b: &Mat<ZpElement>) -> Mat<ZpElement> {
    assert_eq!(mat_a.shape, mat_b.shape, "shape mismatch in mat_addition");

    let mut acc: HashMap<(usize, usize), ZpElement> = HashMap::new();
    for (r, c, v) in mat_a.data.iter().chain(mat_b.data.iter()) {
        acc.entry((*r, *c))
            .and_modify(|existing| *existing = *existing + *v)
            .or_insert(*v);
    }

    let data = acc.into_iter().map(|((r, c), v)| (r, c, v)).collect();
    Mat::new_from_data_vec(&mat_a.id, mat_a.shape, data)
}

pub fn fold_matrix<T: ConvertToZp, U: ConvertToZp>(
    mat_1: &Mat<T>, mat_2: &Mat<U>, r: ZpElement,
) -> Mat<ZpElement> {
    let scaled_2 = mat_scalar_mul(&mat_to_zp(mat_2), r);
    mat_addition(&mat_to_zp(mat_1), &scaled_2)
}

pub fn mat_col_to_dense_zp<T: ConvertToZp>(mat: &Mat<T>) -> Vec<ZpElement> {
    assert_eq!(mat.shape.1, 1, "expected a column vector");
    let mut dense = vec![ZpElement::from(0u64); mat.shape.0];
    for (row, col, val) in mat.data.iter() {
        assert_eq!(*col, 0);
        dense[*row] = val.to_zp();
    }
    dense
}

pub fn dense_zp_to_mat_col(id: &str, vec: &Vec<ZpElement>) -> Mat<ZpElement> {
    let data = vec.iter().enumerate()
        .filter(|(_, v)| **v != ZpElement::from(0u64))
        .map(|(i, v)| (i, 0, *v))
        .collect();
    Mat::new_from_data_vec(id, (vec.len(), 1), data)
}

pub fn fold_matrices<T: ConvertToZp>(mats: &[Mat<T>], weight: ZpElement) -> Mat<ZpElement> {
    assert!(!mats.is_empty());
    let shape = mats[0].shape;
    let mut acc: HashMap<(usize, usize), ZpElement> = HashMap::new();
    let mut w_pow = ZpElement::from(1u64);
    for mat in mats.iter() {
        assert_eq!(mat.shape, shape, "all folded matrices must share a shape");
        for (row, col, val) in mat.data.iter() {
            let contribution = val.to_zp() * w_pow;
            acc.entry((*row, *col))
                .and_modify(|e| *e = *e + contribution)
                .or_insert(contribution);
        }
        w_pow = w_pow * weight;
    }
    let data = acc.into_iter().map(|((r, c), v)| (r, c, v)).collect();
    Mat::new_from_data_vec("folded", shape, data)
}

pub fn fold_matrices_with_powers<T: ConvertToZp>(
    mats: &[Mat<T>],
    powers: &[ZpElement],
) -> Mat<ZpElement> {
    assert_eq!(mats.len(), powers.len());
    assert!(!mats.is_empty());
    let shape = mats[0].shape;
    let mut acc: HashMap<(usize, usize), ZpElement> = HashMap::new();
    for (mat, power) in mats.iter().zip(powers.iter()) {
        assert_eq!(mat.shape, shape, "all folded matrices must share a shape");
        for (row, col, val) in mat.data.iter() {
            let contribution = val.to_zp() * *power;
            acc.entry((*row, *col))
                .and_modify(|e| *e = *e + contribution)
                .or_insert(contribution);
        }
    }
    let data = acc.into_iter().map(|((r, c), v)| (r, c, v)).collect();
    Mat::new_from_data_vec("folded", shape, data)
}

pub fn transpose_mat_zp(mat: &Mat<ZpElement>) -> Mat<ZpElement> {
    let transposed_data = mat
        .data
        .iter()
        .map(|(row, column, value)| (*column, *row, *value))
        .collect();

    let transposed_id = format!("{}_T", mat.id);

    Mat::new_from_data_vec(
        &transposed_id,
        (mat.shape.1, mat.shape.0),
        transposed_data,
    )
}

pub fn first_row_zp(mat: &Mat<ZpElement>) -> Vec<ZpElement> {
    let mut row = vec![ZpElement::from(0u64); mat.shape.1];

    for &(matrix_row, column, value) in &mat.data {
        if matrix_row == 0 {
            row[column] = value;
        }
    }

    row
}

pub fn column_commitment_bases(
    srs: &zkmatrix::setup::SRS,
    length: usize,
) -> Vec<GtElement> {
    assert!(srs.g_hat_vec.len() >= length);
    assert!(!srs.h_hat_vec.is_empty());

    srs.g_hat_vec
        .iter()
        .take(length)
        .map(|g| *g * srs.h_hat_vec[0])
        .collect()
}
// ---------------------------------------------------------------------------
// Dense folding helpers.
//
// The originals above accumulate into a HashMap<(row, col), ZpElement> and
// rebuild a sparse Mat on every step. In the fold that meant one HashMap
// construction per cross-term bucket -- |K| of them, sequentially. The
// matrices here are dense after padding anyway, so a flat Vec accumulator
// with rayon over the matrices is both simpler and orders of magnitude
// cheaper. `fold_matrices_with_powers_dense` is checked against
// `fold_matrices_with_powers` in the tests.
// ---------------------------------------------------------------------------

use rayon::prelude::*;

/// sum_i powers[i] * mats[i], returned as a dense row-major buffer.
pub fn fold_matrices_dense<T: ConvertToZp + Sync>(
    mats: &[Mat<T>],
    powers: &[ZpElement],
) -> (Vec<ZpElement>, (usize, usize)) {
    assert_eq!(mats.len(), powers.len());
    assert!(!mats.is_empty());
    let shape = mats[0].shape;
    let len = shape.0 * shape.1;

    let acc = mats
        .par_iter()
        .zip(powers.par_iter())
        .fold(
            || vec![ZpElement::from(0u64); len],
            |mut acc, (mat, power)| {
                assert_eq!(mat.shape, shape, "all folded matrices must share a shape");
                for (row, col, val) in mat.data.iter() {
                    acc[row * shape.1 + col] += val.to_zp() * *power;
                }
                acc
            },
        )
        .reduce(
            || vec![ZpElement::from(0u64); len],
            |mut a, b| {
                for (x, y) in a.iter_mut().zip(b.iter()) {
                    *x += *y;
                }
                a
            },
        );

    (acc, shape)
}

pub fn dense_to_mat(id: &str, dense: &[ZpElement], shape: (usize, usize)) -> Mat<ZpElement> {
    let zero = ZpElement::from(0u64);
    let data = dense
        .iter()
        .enumerate()
        .filter(|(_, v)| **v != zero)
        .map(|(idx, v)| (idx / shape.1, idx % shape.1, *v))
        .collect();
    Mat::new_from_data_vec(id, shape, data)
}

pub fn fold_matrices_with_powers_dense<T: ConvertToZp + Sync>(
    mats: &[Mat<T>],
    powers: &[ZpElement],
) -> Mat<ZpElement> {
    let (dense, shape) = fold_matrices_dense(mats, powers);
    dense_to_mat("folded", &dense, shape)
}

/// base + sum_k weights[k] * vecs[k], over dense column vectors.
///
/// Replaces the `|K|`-deep chain of `mat_addition(.., mat_scalar_mul(..))`
/// calls that built each folded right-hand side.
pub fn accumulate_weighted_columns(
    base: Vec<ZpElement>,
    vecs: &[Vec<ZpElement>],
    weights: &[ZpElement],
) -> Vec<ZpElement> {
    assert_eq!(vecs.len(), weights.len());
    let len = base.len();
    let contribution = vecs
        .par_iter()
        .zip(weights.par_iter())
        .fold(
            || vec![ZpElement::from(0u64); len],
            |mut acc, (vec, weight)| {
                for (slot, value) in acc.iter_mut().zip(vec.iter()) {
                    *slot += *value * *weight;
                }
                acc
            },
        )
        .reduce(
            || vec![ZpElement::from(0u64); len],
            |mut a, b| {
                for (x, y) in a.iter_mut().zip(b.iter()) {
                    *x += *y;
                }
                a
            },
        );

    base.into_iter()
        .zip(contribution.into_iter())
        .map(|(a, b)| a + b)
        .collect()
}
