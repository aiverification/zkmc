use bls12_381 as bls12;
use blstrs;
use zkmatrix::mat::Mat;
use zkmatrix::utils::curve as bls;

// ============================================== MATRIX-RELATED ==============================================

// Pads height, then pads first row, then pads rest of rows to length of first
// If subsequent row is too long, panics - TODO change this?
pub fn pad_matrix(m: &Vec<Vec<i64>>) -> Vec<Vec<i64>> {
    let mut padded = m.clone();
    while !padded.len().is_power_of_two() || padded.len() == 0 {
        padded.push(vec![0i64]);
    }
    while !padded[0].len().is_power_of_two() {
        padded[0].push(0i64);
    }
    for i in 1..padded.len() {
        if padded[i].len() > padded[0].len() {
            panic!("Padding error - row too long!");
        }
        while padded[i].len() < padded[0].len() {
            padded[i].push(0i64);
        }
    }
    return padded;
}

pub fn pad_matrix_with_a(m: &Vec<Vec<i64>>, a: i64) -> Vec<Vec<i64>> {
    let mut padded = m.clone();
    while !padded.len().is_power_of_two() || padded.len() == 0 {
        padded.push(vec![a]);
    }
    while !padded[0].len().is_power_of_two() {
        padded[0].push(a);
    }
    for i in 1..padded.len() {
        if padded[i].len() > padded[0].len() {
            panic!("Padding error - row too long!");
        }
        while padded[i].len() < padded[0].len() {
            padded[i].push(a);
        }
    }
    return padded;
}

// TODO - add check for consistent width (we assume every row has same width as 0th row) - still todo?
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

pub fn negate_matrix(m: &Vec<Vec<i64>>) -> Vec<Vec<i64>> {
    let mut negative_m = m.clone();
    for i in 0..(negative_m.len()) {
        for j in 0..(negative_m[i].len()) {
            negative_m[i][j] = -1 * negative_m[i][j];
        }
    }
    return negative_m;
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

pub fn vec_mat_to_zkmatrix_i128(name: String, m: &Vec<Vec<i64>>) -> Mat<i128> {
    let mut elems: Vec<(usize, usize, i128)> = vec![];
    for i in 0..(m.len()) {
        for j in 0..(m[i].len()) {
            elems.push((i, j, m[i][j] as i128));
        }
    }
    return Mat::<i128>::new_from_data_vec(&name, (m.len(), m[0].len()), elems);
}

// ============================================== CURVE-RELATED ==============================================
// Need for conversion of randomness!
pub fn blstrs_to_bls_field_elem(zp: &blstrs::Scalar) -> bls::ZpElement {
    let bytes = zp.to_bytes_le();
    // Pad to 32 bytes if needed
    let mut buf = [0u8; 32];
    buf[(32 - bytes.len())..].copy_from_slice(&bytes);
    let scalar = bls12_381::Scalar::from_bytes(&buf).unwrap();
    return bls::ZpElement { value: scalar };
}

pub fn blstrs_proj_to_bls_g1(g: &blstrs::G1Projective) -> bls::G1Element {
    let g_aff = g.to_compressed();
    let bls_g_aff = bls12::G1Affine::from_compressed(&g_aff).unwrap();
    let bls_g = bls12::G1Projective::from(bls_g_aff);
    return bls::G1Element { value: bls_g };
}

pub fn blstrs_affine_to_bls_g1(g: &blstrs::G1Affine) -> bls::G1Element {
    let bls_g_aff = bls12::G1Affine::from_compressed(&g.to_compressed()).unwrap();
    return bls::G1Element {
        value: bls12::G1Projective::from(bls_g_aff),
    };
}

pub fn get_bls_g1_zero() -> bls::G1Element {
    let P = bls::G1Element::generator();
    return P - P;
}

pub fn get_bls_gt_zero() -> bls::GtElement {
    let P = bls::GtElement::generator();
    return P - P;
}
