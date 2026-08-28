//! End-to-end tests over a small synthetic instance.
//!
//! The honest case must verify; each tampered case must be rejected, and by
//! the check that is supposed to catch it. `fold_prover_verifier_agree` is
//! the strongest regression check here: it pins that the verifier
//! reconstructs exactly the folded commitments the prover proved against.

use bls_bulletproofs::{BulletproofGens, PedersenGens};
use zkmatrix::setup::SRS;
use zkmatrix::utils::curve::{G1Element, ZpElement};

use zkmc_symbolic_folding::commit::col_comm_to_gt;
use zkmc_symbolic_folding::folding::*;
use zkmc_symbolic_folding::utils::curve_utils::*;
use zkmc_symbolic_folding::utils::plain_utils::*;
use zkmc_symbolic_folding::utils::public_exponent_schedule::*;
use zkmc_symbolic_folding::zkp;
use zkmc_symbolic_folding::zkrp;

const MAX_M: usize = 4;
const MAX_N: usize = 8;
const N: usize = 4;

/// A small instance satisfying every relation the protocol checks.
///
/// mu_i is the first unit vector, so choosing -G_i's first column to be e_i
/// makes (-G_i) mu_i = e_i hold by construction; the rest is arithmetic.
fn build_input() -> zkp::ZKPInput {
    let mut a_i = Vec::new();
    let mut neg_b_i = Vec::new();
    let mut lambda_i = Vec::new();
    let mut neg_g_i = Vec::new();
    let mut neg_h_i = Vec::new();
    let mut mu_i = Vec::new();
    let mut e_i = Vec::new();
    let mut alpha_i = Vec::new();
    let mut beta_i = Vec::new();

    for obligation in 0..N {
        let a: Vec<Vec<i64>> = (0..MAX_M)
            .map(|r| {
                (0..MAX_N)
                    .map(|c| ((r * 3 + c * 5 + obligation) as i64 % 11) - 5)
                    .collect()
            })
            .collect();
        let lambda: Vec<Vec<i64>> = (0..MAX_N)
            .map(|r| vec![((r + obligation) as i64 % 7) + 1])
            .collect();
        let e = multiply_matrices_naive(&a, &lambda);

        // mu = e_0, so (-G) mu is the first column of -G.
        let mut mu = vec![vec![0i64]; MAX_N];
        mu[0][0] = 1;
        // Column 0 carries e so that (-G) mu = e; the remaining columns are
        // non-zero so that perturbing mu really does break the relation.
        let neg_g: Vec<Vec<i64>> = (0..MAX_M)
            .map(|r| {
                let mut row: Vec<i64> = (0..MAX_N).map(|c| ((r + c) as i64 % 5) + 1).collect();
                row[0] = e[r][0];
                row
            })
            .collect();

        let neg_b: Vec<Vec<i64>> = vec![(0..MAX_N)
            .map(|c| ((c + obligation) as i64 % 4) + 1)
            .collect()];
        let neg_h: Vec<Vec<i64>> = vec![{
            let mut row: Vec<i64> = (0..MAX_N).map(|c| (c as i64 % 3) + 1).collect();
            row[0] = 2;
            row
        }];

        let alpha = multiply_matrices_naive(&neg_b, &lambda);
        let beta = multiply_matrices_naive(&neg_h, &mu);

        assert_eq!(multiply_matrices_naive(&a, &lambda), e);
        assert_eq!(multiply_matrices_naive(&neg_g, &mu), e);
        assert!(alpha[0][0] + beta[0][0] - 1 >= 0);

        a_i.push(a);
        neg_b_i.push(neg_b);
        lambda_i.push(lambda);
        neg_g_i.push(neg_g);
        neg_h_i.push(neg_h);
        mu_i.push(mu);
        e_i.push(e);
        alpha_i.push(alpha);
        beta_i.push(beta);
    }

    zkp::ZKPInput {
        A_i: a_i,
        neg_b_i,
        lambda_i,
        neg_G_i: neg_g_i,
        neg_h_i,
        mu_i,
        e_i,
        alpha_i,
        beta_i,
    }
}

fn build_params() -> (zkp::ZKPParams, ZpElement) {
    let schedule = salem_spencer_schedule(N);
    let mut pc_gens = PedersenGens::default();
    let g_blstrs: blstrs::G1Affine = pc_gens.B.into();
    let g_bls = blstrs_affine_to_bls_g1(&g_blstrs);
    let aggregation_size = N.next_power_of_two();
    let bp_gens = BulletproofGens::new(32, (MAX_M * MAX_N).max(aggregation_size));
    let q = MAX_M.max(MAX_N) + 1;
    let (srs, s_hat) = SRS::new_with_chosen_g_return_s_hat(q, g_bls);
    let blind_factor_zp = s_hat.pow((q * q) as u64);
    pc_gens.B_blinding = pc_gens.B * bls_field_elem_to_blstrs_scalar(&blind_factor_zp);

    let g_prime = srs.h_hat;
    let g_prime_alpha = g_prime * s_hat;
    let h = blstrs_proj_to_bls_g1(&pc_gens.B_blinding);
    let h_prime = g_prime * blind_factor_zp;

    let zkrp_params = |l: usize, m: usize, n: usize| zkrp::ZKRPParams {
        l,
        m,
        n,
        g_blstrs,
        g_bls,
        g_prime,
        g_prime_alpha,
        g_i: vec![],
        h,
        h_prime,
        zk_matrix_srs: srs.clone(),
        pc_gens: pc_gens.clone(),
        bp_gens: bp_gens.clone(),
    };

    let (pairs, committer) = zkp::ZKPParams::derive(&srs, &schedule, MAX_M, MAX_N);
    let lambda_mu_zkrp_pp = zkrp_params(MAX_N, MAX_N, 1);
    let a_zkrp_pp = zkrp_params(MAX_M * MAX_N, MAX_M, MAX_N);
    let b_zkrp_pp = zkrp_params(MAX_N, 1, MAX_N);
    let params = zkp::ZKPParams {
        srs: srs.clone(),
        schedule,
        pairs,
        committer,
        pc_gens,
        bp_gens,
        big_M: 2u32.pow(31) - 1,
        max_m: MAX_M,
        max_n: MAX_N,
        N,
        aggregation_size,
        lambda_mu_zkrp_pp,
        A_zkrp_pp: a_zkrp_pp,
        b_zkrp_pp,
    };
    (params, s_hat)
}

fn statement_of(input: &zkp::ZKPInput) -> zkp::ZKPStatement {
    zkp::ZKPStatement {
        neg_G_i: input.neg_G_i.clone(),
        neg_h_i: input.neg_h_i.clone(),
    }
}

#[test]
fn honest_proof_verifies() {
    let (params, s_hat) = build_params();
    let input = build_input();
    let statement = statement_of(&input);
    let (proof, _) = zkp::prove(&params, s_hat, &input);
    let (ok, _) = proof.verify(&params, &statement);
    assert!(ok, "honest proof did not verify");
}

/// The verifier must reconstruct exactly the folded commitments the prover
/// proved against -- in every family, on both sides of the pairing.
#[test]
fn fold_prover_verifier_agree() {
    let (params, _) = build_params();
    let input = build_input();
    let srs = &params.srs;
    let ctx = FoldContext {
        srs,
        schedule: &params.schedule,
        pairs: &params.pairs,
        committer: &params.committer,
    };

    let commitments = commit_obligations(
        &input.A_i,
        &input.neg_b_i,
        &input.lambda_i,
        &input.mu_i,
        &input.e_i,
        &input.alpha_i,
        &input.beta_i,
        srs,
        &params.committer,
    );

    let prover = prover_fold(
        &input.A_i,
        &input.neg_b_i,
        &input.neg_G_i,
        &input.neg_h_i,
        &input.lambda_i,
        &input.mu_i,
        &input.e_i,
        &input.alpha_i,
        &input.beta_i,
        &commitments,
        &ctx,
    );
    let verifier = verifier_fold(
        &commitments,
        &prover.cross_a,
        &prover.cross_b,
        &prover.cross_g,
        &prover.cross_h,
        &input.neg_G_i,
        &input.neg_h_i,
        &ctx,
    );

    assert_eq!(prover.challenge, verifier.challenge, "challenge mismatch");
    assert_eq!(prover.a_star.blind, verifier.a_star_blind, "A* mismatch");
    assert_eq!(prover.b_star.blind, verifier.b_star_blind, "b* mismatch");
    assert_eq!(
        prover.lambda_star.blind, verifier.lambda_star_blind,
        "lambda* mismatch"
    );
    assert_eq!(prover.mu_star.blind, verifier.mu_star_blind, "mu* mismatch");
    assert_eq!(prover.e_a_star.blind, verifier.e_a_star_blind, "e_A* mismatch");
    assert_eq!(prover.e_g_star.blind, verifier.e_g_star_blind, "e_G* mismatch");
    assert_eq!(
        prover.alpha_star.blind, verifier.alpha_star_blind,
        "alpha* mismatch"
    );
    assert_eq!(prover.beta_star.blind, verifier.beta_star_blind, "beta* mismatch");
    assert_eq!(prover.g_star.shape, verifier.g_star.shape, "G* shape mismatch");
    assert_eq!(prover.h_star.shape, verifier.h_star.shape, "h* shape mismatch");

    // The folded relations themselves must hold on the folded values.
    let e_a = column_of(&prover.e_a_star.mat, MAX_M);
    let a_lambda = matrix_times_column(&prover.a_star.mat, &column_of(&prover.lambda_star.mat, MAX_N));
    assert_eq!(a_lambda, e_a, "A* lambda* != e_A*");

    let e_g = column_of(&prover.e_g_star.mat, MAX_M);
    let g_mu = matrix_times_column(&prover.g_star, &column_of(&prover.mu_star.mat, MAX_N));
    assert_eq!(g_mu, e_g, "G* mu* != e_G*");

    let alpha = column_of(&prover.alpha_star.mat, 1);
    let b_lambda = matrix_times_column(&prover.b_star.mat, &column_of(&prover.lambda_star.mat, MAX_N));
    assert_eq!(b_lambda, alpha, "b* lambda* != alpha*");

    let beta = column_of(&prover.beta_star.mat, 1);
    let h_mu = matrix_times_column(&prover.h_star, &column_of(&prover.mu_star.mat, MAX_N));
    assert_eq!(h_mu, beta, "h* mu* != beta*");

    // And the G1 commitments really are the Gt ones.
    assert_eq!(
        col_comm_to_gt(prover.lambda_star.blind, srs),
        prover.lambda_star.blind_gt(srs)
    );
}

fn column_of(mat: &zkmatrix::mat::Mat<ZpElement>, len: usize) -> Vec<ZpElement> {
    let mut dense = vec![ZpElement::from(0u64); len];
    for (row, col, value) in mat.data.iter() {
        assert_eq!(*col, 0);
        dense[*row] = *value;
    }
    dense
}

fn matrix_times_column(
    mat: &zkmatrix::mat::Mat<ZpElement>,
    vector: &[ZpElement],
) -> Vec<ZpElement> {
    let mut out = vec![ZpElement::from(0u64); mat.shape.0];
    for (row, col, value) in mat.data.iter() {
        out[*row] += *value * vector[*col];
    }
    out
}

// ---------------------------------------------------------------------------
// Negative cases
// ---------------------------------------------------------------------------

#[test]
fn wrong_e_is_rejected() {
    let (params, s_hat) = build_params();
    let mut input = build_input();
    let statement = statement_of(&input);
    // e_2 no longer equals A_2 lambda_2, so the folded A-family relation
    // fails at the diagonal exponent 2*c_2.
    input.e_i[2][0][0] += 1;
    let (proof, _) = zkp::prove(&params, s_hat, &input);
    let (ok, _) = proof.verify(&params, &statement);
    assert!(!ok, "a proof with a wrong e_i verified");
}

#[test]
fn wrong_alpha_is_rejected() {
    let (params, s_hat) = build_params();
    let mut input = build_input();
    let statement = statement_of(&input);
    input.alpha_i[1][0][0] += 1;
    let (proof, _) = zkp::prove(&params, s_hat, &input);
    let (ok, _) = proof.verify(&params, &statement);
    assert!(!ok, "a proof with a wrong alpha_i verified");
}

/// The public families are now discharged only by zkmmeq, so a mu that does
/// not satisfy G mu = e must be caught there.
#[test]
fn wrong_mu_is_rejected() {
    let (params, s_hat) = build_params();
    let mut input = build_input();
    let statement = statement_of(&input);
    input.mu_i[3][1][0] += 1;
    // beta must stay consistent with the committed mu, so only the public
    // matrix relation is broken.
    input.beta_i[3] = multiply_matrices_naive(&input.neg_h_i[3], &input.mu_i[3]);
    let (proof, _) = zkp::prove(&params, s_hat, &input);
    let (ok, _) = proof.verify(&params, &statement);
    assert!(!ok, "a proof with a wrong mu_i verified");
}

#[test]
fn tampered_cross_term_commitment_is_rejected() {
    let (params, s_hat) = build_params();
    let input = build_input();
    let statement = statement_of(&input);
    let (mut proof, _) = zkp::prove(&params, s_hat, &input);
    proof.cross_a.blind[0] = proof.cross_a.blind[0] + G1Element::generator();
    let (ok, _) = proof.verify(&params, &statement);
    assert!(!ok, "a proof with a tampered cross-term commitment verified");
}

#[test]
fn tampered_obligation_commitment_is_rejected() {
    let (params, s_hat) = build_params();
    let input = build_input();
    let statement = statement_of(&input);
    let (mut proof, _) = zkp::prove(&params, s_hat, &input);
    proof.commitments.lambda.blind[2] = proof.commitments.lambda.blind[2] + G1Element::generator();
    let (ok, _) = proof.verify(&params, &statement);
    assert!(!ok, "a proof with a tampered lambda commitment verified");
}

/// A negative lambda entry is in range mod p but not in [0, M].
#[test]
fn out_of_range_lambda_is_rejected() {
    let (params, s_hat) = build_params();
    let mut input = build_input();
    input.lambda_i[0][0][0] = -3;
    input.e_i[0] = multiply_matrices_naive(&input.A_i[0], &input.lambda_i[0]);
    input.alpha_i[0] = multiply_matrices_naive(&input.neg_b_i[0], &input.lambda_i[0]);
    // Keep the public family consistent with the new e_0.
    for row in 0..MAX_M {
        input.neg_G_i[0][row][0] = input.e_i[0][row][0];
    }
    let statement = statement_of(&input);
    let (proof, _) = zkp::prove(&params, s_hat, &input);
    let (ok, _) = proof.verify(&params, &statement);
    assert!(!ok, "a proof with an out-of-range lambda verified");
}

/// Swapping in a different A_i commitment that carries no range proof must
/// not be accepted.
#[test]
fn uncertified_a_commitment_is_rejected() {
    let (params, s_hat) = build_params();
    let input = build_input();
    let statement = statement_of(&input);
    let (mut proof, _) = zkp::prove(&params, s_hat, &input);
    proof.commitments.a.blind[0] = proof.commitments.a.blind[0] + params.srs.blind_base;
    let (ok, _) = proof.verify(&params, &statement);
    assert!(!ok, "a proof with an uncertified A_i commitment verified");
}
