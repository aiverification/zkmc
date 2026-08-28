//! Equivalence tests for the primitives replaced during the folding
//! optimisation.
//!
//! Each new implementation is checked against the one it replaces, on the
//! same inputs, so the rewrites are pinned pointwise rather than only
//! end-to-end.

use zkmatrix::mat::Mat;
use zkmatrix::setup::SRS;
use zkmatrix::utils::curve::{G1Element, GtElement, ZpElement};

use zkmc_symbolic_folding::commit::*;
use zkmc_symbolic_folding::utils::msm::*;
use zkmc_symbolic_folding::utils::public_exponent_schedule::*;
use zkmc_symbolic_folding::utils::zk_utils::*;

fn test_srs(q: usize) -> SRS {
    let (srs, _) = SRS::new_with_chosen_g_return_s_hat(q, G1Element::generator());
    srs
}

fn random_column(len: usize) -> Vec<ZpElement> {
    (0..len).map(|_| ZpElement::rand()).collect()
}

fn dense_of(mat: &Mat<ZpElement>) -> Vec<ZpElement> {
    let mut dense = vec![ZpElement::from(0u64); mat.shape.0 * mat.shape.1];
    for (row, col, value) in mat.data.iter() {
        dense[row * mat.shape.1 + col] = *value;
    }
    dense
}

fn random_mat(id: &str, rows: usize, cols: usize) -> Mat<i64> {
    let data = (0..rows)
        .flat_map(|r| (0..cols).map(move |c| (r, c, ((r * 31 + c * 17) as i64) - 200)))
        .collect();
    Mat::<i64>::new_from_data_vec(id, (rows, cols), data)
}

/// The load-bearing fact behind the G1 representation: blind_base is a
/// pairing of an SRS element already published in g_hat_prime_vec against
/// the same h_hat_vec[0] every column commitment uses.
#[test]
fn g1_blind_base_matches_blind_base() {
    for q in [9usize, 17, 33] {
        let srs = test_srs(q);
        assert_eq!(
            g1_blind_base(&srs) * srs.h_hat_vec[0],
            srs.blind_base,
            "g_hat_prime_vec.last() is not the G1 preimage of blind_base at q = {q}"
        );
    }
}

/// A blinded column commitment in G1 form pairs to exactly the target-group
/// commitment the previous code produced.
#[test]
fn col_comm_g1_matches_gt() {
    let srs = test_srs(33);
    for len in [1usize, 4, 16, 32] {
        let committer = ColCommitter::new(&srs, len);
        for _ in 0..3 {
            let values = random_column(len);
            let randomness = ZpElement::rand();
            let g1 = committer.commit(&values, randomness);
            let expected = commit_column_vector_zp_blinded_gt(&values, &srs, randomness);
            assert_eq!(
                col_comm_to_gt(g1, &srs),
                expected,
                "G1 column commitment disagrees with the Gt one at len = {len}"
            );
        }
    }
}

/// A zero-length vector and an all-zero vector must still commit to the
/// blinding term alone, which is where an MSM backend is most likely to
/// mishandle the point at infinity.
#[test]
fn col_comm_handles_zero_values() {
    let srs = test_srs(17);
    let committer = ColCommitter::new(&srs, 8);
    let zeros = vec![ZpElement::from(0u64); 8];
    let randomness = ZpElement::rand();
    assert_eq!(
        col_comm_to_gt(committer.commit(&zeros, randomness), &srs),
        commit_column_vector_zp_blinded_gt(&zeros, &srs, randomness)
    );
    let zero_r = committer.commit(&zeros, ZpElement::from(0u64));
    assert_eq!(
        col_comm_to_gt(zero_r, &srs),
        commit_column_vector_zp_blinded_gt(&zeros, &srs, ZpElement::from(0u64))
    );
}

/// msm_g1 equals scalar-multiply-and-add, including at the sizes either side
/// of the Pippenger cutoff and with identity points in the mix.
#[test]
fn msm_matches_naive() {
    let generator = G1Element::generator();
    let identity = generator - generator;
    for len in [0usize, 1, 7, 8, 9, 64, 300] {
        let points: Vec<G1Element> = (0..len)
            .map(|i| {
                if i % 11 == 3 {
                    identity
                } else {
                    generator * ZpElement::from((i as u64) + 1)
                }
            })
            .collect();
        let scalars: Vec<ZpElement> = (0..len)
            .map(|i| {
                if i % 7 == 5 {
                    ZpElement::from(0u64)
                } else {
                    ZpElement::rand()
                }
            })
            .collect();

        let mut naive = identity;
        for (point, scalar) in points.iter().zip(scalars.iter()) {
            naive = naive + (*point * *scalar);
        }
        assert_eq!(msm_g1(&points, &scalars), naive, "msm_g1 mismatch at n = {len}");
    }
}

#[test]
fn msm_pair_matches_concatenation() {
    let generator = G1Element::generator();
    let points_a: Vec<G1Element> = (0..20).map(|i| generator * ZpElement::from((i as u64) + 1)).collect();
    let points_b: Vec<G1Element> = (0..37).map(|i| generator * ZpElement::from((i as u64) + 91)).collect();
    let scalars_a = random_column(20);
    let scalars_b = random_column(37);

    let mut all_points = points_a.clone();
    all_points.extend_from_slice(&points_b);
    let mut all_scalars = scalars_a.clone();
    all_scalars.extend_from_slice(&scalars_b);

    assert_eq!(
        msm_g1_pair(&points_a, &scalars_a, &points_b, &scalars_b),
        msm_g1(&all_points, &all_scalars)
    );
}

/// The dense fold reproduces the HashMap-based one exactly.
#[test]
fn fold_dense_matches_hashmap() {
    let mats: Vec<Mat<i64>> = (0..6).map(|i| random_mat(&format!("m{i}"), 5, 7)).collect();
    let powers = random_column(6);
    let expected = fold_matrices_with_powers(&mats, &powers);
    let actual = fold_matrices_with_powers_dense(&mats, &powers);
    assert_eq!(actual.shape, expected.shape);
    assert_eq!(dense_of(&actual), dense_of(&expected));
}

/// The weighted column accumulator reproduces the mat_addition chain.
#[test]
fn accumulate_columns_matches_mat_addition() {
    let len = 9usize;
    let base = random_column(len);
    let vecs: Vec<Vec<ZpElement>> = (0..12).map(|_| random_column(len)).collect();
    let weights = random_column(12);

    let mut expected = dense_zp_to_mat_col("base", &base);
    for (vec, weight) in vecs.iter().zip(weights.iter()) {
        expected = mat_addition(
            &expected,
            &mat_scalar_mul(&dense_zp_to_mat_col("k", vec), *weight),
        );
    }

    let actual = accumulate_weighted_columns(base, &vecs, &weights);
    assert_eq!(actual, mat_col_to_dense_zp(&expected));
}

/// CrossTermPairs agrees with compute_k_values, and its representatives let
/// r^k be formed multiplicatively.
#[test]
fn cross_term_pairs_match_schedule() {
    for n in [2usize, 3, 5, 8, 16, 33] {
        let schedule = salem_spencer_schedule(n);
        assert!(schedule_has_no_2c_collisions(&schedule));
        let pairs = CrossTermPairs::new(&schedule);
        assert_eq!(pairs.k_values, compute_k_values(&schedule), "K mismatch at n = {n}");

        let r = ZpElement::rand();
        let r_schedule: Vec<ZpElement> = schedule.iter().map(|c| r.pow(*c as u64)).collect();
        for (idx, (i, j)) in pairs.representatives().iter().enumerate() {
            assert_eq!(schedule[*i] + schedule[*j], pairs.k_values[idx]);
            assert_eq!(
                r_schedule[*i] * r_schedule[*j],
                r.pow(pairs.k_values[idx] as u64),
                "multiplicative r^k mismatch at n = {n}, idx = {idx}"
            );
        }

        // every listed pair really lands in its bucket, and nothing is lost
        let mut total = 0usize;
        for (idx, group) in pairs.pairs.iter().enumerate() {
            for (i, j) in group.iter() {
                assert_ne!(i, j);
                assert_eq!(schedule[*i] + schedule[*j], pairs.k_values[idx]);
            }
            total += group.len();
        }
        assert_eq!(total, n * (n - 1));
    }
}

/// Sanity: the Gt image of a G1 fold equals the Gt fold of the images.
/// This is what lets the verifier fold in G1 and pair once at the end.
#[test]
fn g1_fold_commutes_with_pairing() {
    let srs = test_srs(17);
    let committer = ColCommitter::new(&srs, 8);
    let commitments: Vec<G1Element> = (0..25)
        .map(|_| committer.commit(&random_column(8), ZpElement::rand()))
        .collect();
    let weights = random_column(25);

    let folded_g1 = msm_g1(&commitments, &weights);

    let mut folded_gt = {
        let zero = GtElement::generator();
        zero - zero
    };
    for (commitment, weight) in commitments.iter().zip(weights.iter()) {
        folded_gt = folded_gt + (col_comm_to_gt(*commitment, &srs) * *weight);
    }

    assert_eq!(col_comm_to_gt(folded_g1, &srs), folded_gt);
}

/// The fixed-base comb agrees with the general multi-scalar path, including
/// with zero digits and a short scalar list.
#[test]
fn comb_matches_msm() {
    let generator = G1Element::generator();
    let bases: Vec<G1Element> = (0..17)
        .map(|i| generator * ZpElement::from((i as u64) + 3))
        .collect();
    let table = FixedBaseTable::new(&bases);

    for len in [1usize, 4, 8, 17] {
        let scalars: Vec<ZpElement> = (0..len)
            .map(|i| {
                if i % 5 == 2 {
                    ZpElement::from(0u64)
                } else {
                    ZpElement::rand()
                }
            })
            .collect();
        let blstrs_scalars: Vec<blstrs::Scalar> = scalars.iter().map(zp_to_blstrs).collect();

        let mut naive = generator - generator;
        for (base, scalar) in bases.iter().zip(scalars.iter()) {
            naive = naive + (*base * *scalar);
        }
        assert_eq!(
            blstrs_to_g1(&table.combine(&blstrs_scalars)),
            naive,
            "comb mismatch at len = {len}"
        );

        // ... and with an extra scalar against a base past the prefix.
        let extra = ZpElement::rand();
        let with_extra = table.combine_with(&blstrs_scalars, &[(16, zp_to_blstrs(&extra))]);
        assert_eq!(
            blstrs_to_g1(&with_extra),
            naive + (bases[16] * extra),
            "comb_with mismatch at len = {len}"
        );
    }

    // An all-zero combination must be the identity.
    let zeros: Vec<blstrs::Scalar> = (0..8).map(|_| zp_to_blstrs(&ZpElement::from(0u64))).collect();
    assert_eq!(blstrs_to_g1(&table.combine(&zeros)), generator - generator);
}
