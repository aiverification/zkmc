use zkmatrix::mat::Mat;
use zkmatrix::setup::SRS;
use zkmatrix::utils::curve::{ZpElement, G1Element, G2Element, GtElement};
use zkmatrix::utils::dirac::{self, BraKet};
use crate::utils::curve_utils::get_bls_g1_zero;

// Mimic zkmatrix's commitments but for Mat<ZpElement>
pub fn commit_row_major_zp(
    mat: &Mat<ZpElement>,
    g_base_vec: &Vec<G1Element>,
    h_base_vec: &Vec<G2Element>,
) -> (GtElement, Vec<G2Element>) {
    let right_cache = mat.ket(h_base_vec);
    let result = dirac::inner_product(g_base_vec, &right_cache);
    return (result, right_cache);
}

// Column-major two-tier commitment for a Mat<ZpElement>
pub fn commit_col_major_zp(
    mat: &Mat<ZpElement>,
    g_base_vec: &Vec<G1Element>,
    h_base_vec: &Vec<G2Element>,
) -> (GtElement, Vec<G1Element>) {
    let left_cache = mat.bra(g_base_vec);
    let result = dirac::inner_product(&left_cache, h_base_vec);
    return (result, left_cache);
}

// Convenience wrappers matching the crate's commit_rm/commit_cm naming
pub fn commit_rm_zp(mat: &Mat<ZpElement>, srs: &SRS) -> (GtElement, Vec<G2Element>) {
    commit_row_major_zp(mat, &srs.g_hat_vec, &srs.h_hat_vec)
}

pub fn commit_cm_zp(mat: &Mat<ZpElement>, srs: &SRS) -> (GtElement, Vec<G1Element>) {
    commit_col_major_zp(mat, &srs.g_hat_vec, &srs.h_hat_vec)
}

pub fn commit_rm_i64(
    mat: &Mat<i64>,
    srs: &SRS,
) -> (GtElement, Vec<G2Element>) {
    let right_cache = mat.ket(&srs.h_hat_vec);
    let commitment = dirac::inner_product(&srs.g_hat_vec, &right_cache);
    (commitment, right_cache)
}

pub fn commit_cm_i64(
    mat: &Mat<i64>,
    srs: &SRS,
) -> (GtElement, Vec<G1Element>) {
    let left_cache = mat.bra(&srs.g_hat_vec);
    let commitment = dirac::inner_product(&left_cache, &srs.h_hat_vec);
    (commitment, left_cache)
}

pub fn commit_column_vector_zp(
    values: &[ZpElement],
    srs: &SRS,
) -> (GtElement, Vec<G1Element>) {
    let mut left_cache = get_bls_g1_zero();

    for (value, base) in values.iter().zip(srs.g_hat_vec.iter()) {
        left_cache += *value * *base;
    }

    let commitment = left_cache * srs.h_hat_vec[0];

    (commitment, vec![left_cache])
}
// ---------------------------------------------------------------------------
// G1 form for column-vector commitments.
//
// Every commitment this protocol makes to a column vector -- lambda_i, mu_i,
// e_i, alpha_i, beta_i and all four families of cross terms -- is
// `inner_product(bra(g_hat_vec), h_hat_vec)`, and `bra` of an m x 1 matrix
// yields a single G1 point. So the commitment is `e(P, h_hat_vec[0])` for
// one point P.
//
// The blinding term collapses the same way. In `SRS::new_*`,
//     blind_base       = s^(q^2) * g_hat * h_hat
//     h_hat_vec[0]     = s^q * h_hat
//     g_hat_prime_vec  = [s^q, s^2q, ..., s^((q-1)q)] * g_hat
// so `g_hat_prime_vec.last() = s^(q^2 - q) * g_hat` pairs with
// `h_hat_vec[0]` to give exactly `blind_base`. No new SRS element is needed;
// `g1_blind_base_matches_blind_base` pins this down.
//
// Carrying P instead of its Gt image removes one pairing and one Gt scalar
// multiplication per commitment on the prover, turns the verifier's folds
// into G1 multi-scalar multiplications, and shrinks those proof elements
// from 576 to 48 bytes. Pairing against a fixed non-trivial h_hat_vec[0] is
// injective, so binding is unaffected.
// ---------------------------------------------------------------------------

use zkmatrix::utils::curve::ConvertToZp;
use rayon::prelude::*;
use crate::utils::msm::{zp_to_blstrs, blstrs_to_g1, from_blstrs_batch, FixedBaseTable};

/// The G1 preimage of `srs.blind_base` under pairing with `srs.h_hat_vec[0]`.
pub fn g1_blind_base(srs: &SRS) -> G1Element {
    *srs.g_hat_prime_vec
        .last()
        .expect("SRS has no g_hat_prime_vec entries")
}

/// The target-group value of a column-vector commitment held in G1 form.
pub fn col_comm_to_gt(commitment: G1Element, srs: &SRS) -> GtElement {
    commitment * srs.h_hat_vec[0]
}

/// Commits to column vectors of a fixed length against a fixed base prefix.
pub struct ColCommitter {
    /// The g_hat_vec prefix followed by the blinding base, so a commitment
    /// is a single comb over the whole set.
    table: FixedBaseTable,
    blind_index: usize,
}

impl ColCommitter {
    pub fn new(srs: &SRS, length: usize) -> Self {
        assert!(
            srs.g_hat_vec.len() >= length,
            "SRS too small for a column of length {length}"
        );
        let mut points = srs.g_hat_vec[..length].to_vec();
        points.push(g1_blind_base(srs));
        Self {
            table: FixedBaseTable::new(&points),
            blind_index: length,
        }
    }

    pub fn len(&self) -> usize {
        self.blind_index
    }

    /// Com(values; randomness), left in the backend's representation.
    fn commit_raw(&self, values: &[ZpElement], randomness: ZpElement) -> blstrs::G1Projective {
        let scalars: Vec<blstrs::Scalar> = values.iter().map(zp_to_blstrs).collect();
        self.table.combine_with(
            &scalars,
            &[(self.blind_index, zp_to_blstrs(&randomness))],
        )
    }

    /// Com(values; randomness), in G1 form.
    pub fn commit(&self, values: &[ZpElement], randomness: ZpElement) -> G1Element {
        blstrs_to_g1(&self.commit_raw(values, randomness))
    }

    /// Commits to many column vectors in parallel.
    ///
    /// The conversion back out of the backend is batched, so the whole set
    /// costs one field inversion rather than one each.
    pub fn commit_many(
        &self,
        values: &[Vec<ZpElement>],
        randomness: &[ZpElement],
    ) -> Vec<G1Element> {
        assert_eq!(values.len(), randomness.len());
        if values.is_empty() {
            return Vec::new();
        }
        let raw: Vec<blstrs::G1Projective> = values
            .par_iter()
            .zip(randomness.par_iter())
            .map(|(v, r)| self.commit_raw(v, *r))
            .collect();
        from_blstrs_batch(&raw)
    }
}

/// Dense column vector of a single-column `Mat`, in Zp.
pub fn column_values<T: ConvertToZp>(mat: &Mat<T>) -> Vec<ZpElement> {
    assert_eq!(mat.shape.1, 1, "expected a column vector");
    let mut dense = vec![ZpElement::from(0u64); mat.shape.0];
    for (row, _, value) in mat.data.iter() {
        dense[*row] = value.to_zp();
    }
    dense
}

/// Reference implementation of the G1 form, used by the equivalence tests:
/// the Gt commitment built the old way, from the same inputs.
pub fn commit_column_vector_zp_blinded_gt(
    values: &[ZpElement],
    srs: &SRS,
    randomness: ZpElement,
) -> GtElement {
    let (commitment, _) = commit_column_vector_zp(values, srs);
    commitment + (randomness * srs.blind_base)
}

