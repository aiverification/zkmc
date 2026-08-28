use rayon::prelude::*;
use zkmatrix::{
    mat::Mat,
};

// Pads matrix mat of size m' x n' ti size m x n (m, n must be powers of two)
pub fn pad_matrix_to_size(mat: &Vec<Vec<i64>>, m: usize, n: usize) -> Vec<Vec<i64>> {
    assert!(m.is_power_of_two() && n.is_power_of_two());
    let mut padded = mat.clone();
    while padded.len() < m{
        padded.push(vec![0i64; n]);
    }
    padded.par_iter_mut().for_each(|row| {
        while row.len() < n {
            row.push(0i64);
        }
    });
    return padded;
}

pub fn pad_matrix_to_size_in_place(mat: &mut Vec<Vec<i64>>, m: usize, n: usize) {
    assert!(m.is_power_of_two() && n.is_power_of_two());
    // let mut padded = mat.clone();
    while mat.len() < m{
        mat.push(vec![0i64; n]);
    }
    mat.par_iter_mut().for_each(|row| {
        while row.len() < n {
            row.push(0i64);
        }
    });
}

pub fn pad_matrices(
    A_matrices: &mut [Vec<Vec<i64>>],
    b_matrices: &mut [Vec<Vec<i64>>],
    c_matrices: &mut [Vec<Vec<i64>>],
){
    assert!(A_matrices.len() != 0 && b_matrices.len() != 0);
    let mut max_m = A_matrices[0].len();
    let mut max_n = A_matrices[0][0].len();
    for A in A_matrices.iter(){
        max_m = max_m.max(A.len());
        max_n = max_n.max(A[0].len());
    }
    max_m = max_m.next_power_of_two();
    max_n = max_n.next_power_of_two();

    for A in A_matrices.iter_mut() {
        pad_matrix_to_size_in_place(A, max_m, max_n);
    }

    for b in b_matrices.iter_mut() {
        pad_matrix_to_size_in_place(b, max_n, 1);
    }

    for c in c_matrices.iter_mut() {
        pad_matrix_to_size_in_place(c, max_m, 1);
    }
}

pub fn pad_matrices_par(
    A_matrices: &mut [Vec<Vec<i64>>],
    b_matrices: &mut [Vec<Vec<i64>>],
    c_matrices: &mut [Vec<Vec<i64>>],
){
    assert!(A_matrices.len() != 0 && b_matrices.len() != 0);
    let mut max_m = A_matrices[0].len();
    let mut max_n = A_matrices[0][0].len();
    for A in A_matrices.iter(){
        max_m = max_m.max(A.len());
        max_n = max_n.max(A[0].len());
    }
    max_m = max_m.next_power_of_two();
    max_n = max_n.next_power_of_two();

    A_matrices.par_iter_mut()
        .zip(b_matrices.par_iter_mut())
        .zip(c_matrices.par_iter_mut())
        .for_each(|((a, b), c)| {
            a.resize(max_m, vec![0; max_n]);
            a.iter_mut().for_each(|row| row.resize(max_n, 0));

            b.resize(max_n, vec![0; 1]);
            b.iter_mut().for_each(|row| row.resize(1, 0));

            c.resize(max_m, vec![0; 1]);
            c.iter_mut().for_each(|row| row.resize(1, 0));
        }
    );
}

pub fn pad_matrices_par_to_size(
    matrices: &mut [Vec<Vec<i64>>],
    max_m: usize,
    max_n: usize,
){
    matrices.par_iter_mut()
        .for_each(|mat| {
            mat.resize(max_m, vec![0; max_n]);
            mat.iter_mut().for_each(|row| row.resize(max_n, 0));
        }
    );
}

pub fn multiply_matrices_naive(a: &Vec<Vec<i64>>, b: &Vec<Vec<i64>>) -> Vec<Vec<i64>> {
    let mut c: Vec<Vec<i64>> = vec![vec![0]; a.len()];
    for (index, entry) in c.iter_mut().enumerate(){
        for position in 0..b.len(){
            entry[0] += a[index][position] * b[position][0];
        }
    }
    return c;
}

pub fn multiply_matrices_naive_par(a: &Vec<Vec<i64>>, b: &Vec<Vec<i64>>) -> Vec<Vec<i64>> {
    let mut c: Vec<Vec<i64>> = vec![vec![0]; a.len()];
    c.par_iter_mut().zip(a.par_iter()).for_each(|(entry, a_row)| {
        for position in 0..b.len() {
            entry[0] += a_row[position] * b[position][0];
        }
    });
    return c;
}

pub fn add_matrices(a: &Vec<Vec<i64>>, b: &Vec<Vec<i64>>) -> Vec<Vec<i64>> {
    assert!(a.len() == b.len() && a[0].len() == b[0].len());
    let mut c: Vec<Vec<i64>> = vec![vec![0; a[0].len()]; a.len()];
    for i in 0..a.len(){
        for j in 0..a[i].len(){
            c[i][j] = a[i][j] + b[i][j];
        }
    }
    return c;
}

pub fn add_matrices_par(a: &Vec<Vec<i64>>, b: &Vec<Vec<i64>>) -> Vec<Vec<i64>> {
    assert!(a.len() == b.len() && a[0].len() == b[0].len());
    let mut c: Vec<Vec<i64>> = vec![vec![0; a[0].len()]; a.len()];
    c.par_iter_mut()
        .zip(a.par_iter())
        .zip(b.par_iter())
        .for_each(|((c_row, a_row), b_row)| {
            *c_row = a_row.iter().zip(b_row.iter()).map(|(x, y)| x + y).collect()
        });
    return c;
}

pub fn vec_mat_to_zkmatrix_i64(name: String, m: &Vec<Vec<i64>>) -> Mat<i64> {
    let mut elems: Vec<(usize, usize, i64)> = vec![];
    for i in 0..(m.len()) {
        for j in 0..(m[i].len()) {
            elems.push((i, j, m[i][j] as i64));
        }
    }
    return Mat::<i64>::new_from_data_vec(&name, (m.len(), m[0].len()), elems);
}

pub fn transpose_matrix(m: &Vec<Vec<i64>>) -> Vec<Vec<i64>> {
    let mut m_T: Vec<Vec<i64>> = vec![];
    for _ in 0..(m[0].len()) {
        m_T.push(vec![0i64; m.len()]);
    }
    for j in 0..(m[0].len()) {
        for i in 0..(m.len()) {
            m_T[j][i] = m[i][j];
        }
    }
    return m_T;
}

pub fn transpose_matrix_in_place(m: &mut Vec<Vec<i64>>) {
    if m.is_empty() {
        return;
    }

    let rows = m.len();
    let columns = m[0].len();

    assert!(
        m.iter().all(|row| row.len() == columns),
        "transpose_matrix requires a rectangular matrix"
    );

    let original = std::mem::take(m);

    *m = (0..columns)
        .map(|column| {
            (0..rows)
                .map(|row| original[row][column])
                .collect()
        })
        .collect();
}

pub fn negate_matrix(m: &Vec<Vec<i64>>) -> Vec<Vec<i64>> {
    let mut negative_m = m.clone();
    for i in 0..(negative_m.len()) {
        for j in 0..(negative_m[i].len()) {
            negative_m[i][j] = -1 * negative_m[i][j];
        }
    }
    return negative_m;
}