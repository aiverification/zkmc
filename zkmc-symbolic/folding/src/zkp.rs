use bls_bulletproofs::{BulletproofGens, PedersenGens};
use blstrs;
use group::ff::Field;
use merlin::Transcript;
use rayon::prelude::*;
use std::collections::HashMap;
use std::time::Instant;
use zkmatrix::setup::SRS;
use zkmatrix::utils::curve::{G1Element, GtElement, ZpElement};
use zkmatrix::utils::fiat_shamir::TranSeq;
use zkmatrix::zkprotocols::{zk_matmul::ZkMatMul, zk_trans::ZkTranSeqProver};

use crate::commit::*;
use crate::folding::*;
use crate::range_proof::*;
use crate::utils::curve_utils::*;
use crate::utils::plain_utils::*;
use crate::utils::public_exponent_schedule::*;
use crate::utils::zk_utils::mat_col_to_dense_zp;
use crate::zkmmeq;
use crate::zkrp;

// Public parameters for the full ZKP: the SRS, exponent schedule, Pedersen /
// Bulletproof generators, and the ZKRP parameters shared by the range proofs.
// Both prover and verifier receive the same ZKPParams.
pub struct ZKPParams {
    pub srs: SRS,
    pub schedule: Vec<u128>,
    /// Cross-term buckets for the schedule. Shared by both parties so the
    /// O(N^2) pair enumeration happens once rather than once per family.
    pub pairs: CrossTermPairs,
    /// Fixed bases for every column-vector commitment, pre-converted for the
    /// multi-scalar-multiplication backend.
    pub committer: ColCommitter,
    pub pc_gens: PedersenGens,
    pub bp_gens: BulletproofGens,
    pub big_M: u32,
    pub max_m: usize,
    pub max_n: usize,
    pub N: usize,
    pub aggregation_size: usize,
    pub lambda_mu_zkrp_pp: zkrp::ZKRPParams,
    pub A_zkrp_pp: zkrp::ZKRPParams,
    pub b_zkrp_pp: zkrp::ZKRPParams,
}

impl ZKPParams {
    /// Builds the derived fields (`pairs`, `committer`) from the schedule and
    /// SRS so callers cannot get them out of step with each other.
    pub fn derive(srs: &SRS, schedule: &[u128], max_m: usize, max_n: usize) -> (CrossTermPairs, ColCommitter) {
        (
            CrossTermPairs::new(schedule),
            ColCommitter::new(srs, max_m.max(max_n)),
        )
    }
}

// Public statement matrices: the program matrices -G_i and -h_i that the
// verifier needs to reconstruct G* and h* itself. The prover knows these too
// (they are part of the instance), but only the prover sees the witness
// matrices in ZKPInput.
pub struct ZKPStatement {
    pub neg_G_i: Vec<Vec<Vec<i64>>>,
    pub neg_h_i: Vec<Vec<Vec<i64>>>,
}

// The prover's full input: statement + witness matrices. The witness matrices
// (A_i, -b_i, lambda_i, mu_i, e_i, alpha_i, beta_i) are never sent to the
// verifier; they only appear as blinded commitments inside ZKPProof.
pub struct ZKPInput {
    pub A_i: Vec<Vec<Vec<i64>>>,
    pub neg_b_i: Vec<Vec<Vec<i64>>>,
    pub lambda_i: Vec<Vec<Vec<i64>>>,
    pub neg_G_i: Vec<Vec<Vec<i64>>>,
    pub neg_h_i: Vec<Vec<Vec<i64>>>,
    pub mu_i: Vec<Vec<Vec<i64>>>,
    pub e_i: Vec<Vec<Vec<i64>>>,
    pub alpha_i: Vec<Vec<Vec<i64>>>,
    pub beta_i: Vec<Vec<Vec<i64>>>,
}

// Everything the prover sends to the verifier.
//
// The column families and every cross-term commitment travel as G1 points
// (48 bytes) rather than target-group elements (576); `commit.rs` explains
// why that is the same commitment. The two public families are discharged by
// a single zkmmeq rather than two zkmm invocations, matching
// fig:ZKMC-S2-fold, so no commitment to G* or h* appears anywhere.
pub struct ZKPProof {
    pub commitments: ObligationCommitments,
    pub cross_a: CrossTermCommitments,
    pub cross_b: CrossTermCommitments,
    pub cross_g: CrossTermCommitments,
    pub cross_h: CrossTermCommitments,
    // Secret-family zkmm proofs + the (m, n, l) dimensions used
    pub A_lambda_e_proof: TranSeq,
    pub b_lambda_alpha_proof: TranSeq,
    pub A_lambda_e_dims: (usize, usize, usize),
    pub b_lambda_alpha_dims: (usize, usize, usize),
    // Public families, batched
    pub zkmmeq_proof: zkmmeq::EqualProofG1,
    // Range proofs
    pub v_range_proof: ArbitraryUpperRangeProof,
    pub lambda_zkrp_proofs: Vec<zkrp::ZKRPProof>,
    pub mu_zkrp_proofs: Vec<zkrp::ZKRPProof>,
    pub A_zkrp_proofs: Vec<zkrp::ZKRPProof>,
    pub b_zkrp_proofs: Vec<zkrp::ZKRPProof>,
    // Canonical (deduplicated) blinds for the A_i / -b_i range proofs
    pub unique_A_blinds: Vec<GtElement>,
    pub unique_b_blinds: Vec<GtElement>,
}

/// Groups identical matrices and returns, per obligation, its class index.
fn dedupe(mats: &[Vec<Vec<i64>>]) -> (Vec<usize>, Vec<Vec<Vec<i64>>>) {
    let mut index: HashMap<&Vec<Vec<i64>>, usize> = HashMap::new();
    let mut class_of = Vec::with_capacity(mats.len());
    let mut unique: Vec<Vec<Vec<i64>>> = Vec::new();
    for mat in mats.iter() {
        match index.get(mat) {
            Some(&class) => class_of.push(class),
            None => {
                let class = unique.len();
                index.insert(mat, class);
                unique.push(mat.clone());
                class_of.push(class);
            }
        }
    }
    (class_of, unique)
}

// Runs the full prover: deduplicates A_i / -b_i, commits to all matrices,
// folds, proves the two secret-family ZKMM relations and the batched public
// zkmmeq, and produces all the range proofs. s_hat (the SRS "toxic waste") is
// prover-only and is used as the ZKRP alpha.
// Returns the proof plus the total prover time in milliseconds.
pub fn prove(params: &ZKPParams, s_hat: ZpElement, input: &ZKPInput) -> (ZKPProof, u128) {
    let N = params.N;
    let max_m = params.max_m;
    let max_n = params.max_n;
    let big_M = params.big_M;
    let srs = &params.srs;

    // ---- Dedupe A_i and -b_i ----
    // Identical (transposed, padded) matrices share one commitment, one
    // randomness, and one ZKRP range proof. Sound because a commitment is
    // binding to its value; note that sharing the commitment also reveals
    // which obligations carry the same secret matrix.
    let dedupe_timer = Instant::now();
    let (obl_to_A_class, unique_A) = dedupe(&input.A_i);
    let (obl_to_b_class, unique_b) = dedupe(&input.neg_b_i);
    let dedupe_time = dedupe_timer.elapsed().as_millis();
    println!(
        "Deduped A_i: {} unique, -b_i: {} unique (of {} obligations)",
        unique_A.len(),
        unique_b.len(),
        N
    );

    // ---- Commit to all matrices ----
    // A and -b are committed once per distinct matrix and then expanded, so
    // the row-major (and most expensive) commitments cost U rather than N.
    let comm_timer = Instant::now();
    let commitments = commit_obligations_deduped(
        &unique_A,
        &obl_to_A_class,
        &unique_b,
        &obl_to_b_class,
        &input.lambda_i,
        &input.mu_i,
        &input.e_i,
        &input.alpha_i,
        &input.beta_i,
        srs,
        &params.committer,
    );
    let unique_A_blinds: Vec<GtElement> = (0..unique_A.len())
        .map(|u| commitments.a.blind[first_with_class(&obl_to_A_class, u)])
        .collect();
    let unique_A_randomness: Vec<ZpElement> = (0..unique_A.len())
        .map(|u| commitments.a.randomness[first_with_class(&obl_to_A_class, u)])
        .collect();
    let unique_b_blinds: Vec<GtElement> = (0..unique_b.len())
        .map(|u| commitments.neg_b.blind[first_with_class(&obl_to_b_class, u)])
        .collect();
    let unique_b_randomness: Vec<ZpElement> = (0..unique_b.len())
        .map(|u| commitments.neg_b.randomness[first_with_class(&obl_to_b_class, u)])
        .collect();
    let comm_time = comm_timer.elapsed().as_millis();
    println!("Committed matrices");

    // ---- Prover Fold ----
    let ctx = FoldContext {
        srs,
        schedule: &params.schedule,
        pairs: &params.pairs,
        committer: &params.committer,
    };
    let prover_folding_timer = Instant::now();
    let folded = prover_fold(
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
    let prover_folding_time = prover_folding_timer.elapsed().as_millis();

    // ---- Secret families: two zkmm invocations ----
    let lambda_star_blind_gt = folded.lambda_star.blind_gt(srs);

    let prover_proof_A_lambda_e_timer = Instant::now();
    let A_lambda_e_protocol = ZkMatMul::new(
        folded.e_a_star.blind_gt(srs),
        folded.a_star.blind,
        lambda_star_blind_gt,
        folded.e_a_star.mat.shape.0,
        folded.e_a_star.mat.shape.1,
        folded.a_star.mat.shape.1,
    );
    let mut A_lambda_e_prover = ZkTranSeqProver::new(srs);
    A_lambda_e_protocol.prove::<ZpElement, ZpElement, ZpElement>(
        srs,
        &mut A_lambda_e_prover,
        folded.e_a_star.mat.clone(),
        folded.a_star.mat.clone(),
        folded.lambda_star.mat.clone(),
        &folded.e_a_star.cache,
        &folded.a_star.cache,
        &folded.lambda_star.cache,
        folded.e_a_star.randomness,
        folded.a_star.randomness,
        folded.lambda_star.randomness,
    );
    let A_lambda_e_proof = A_lambda_e_prover.publish_trans();
    let A_lambda_e_dims = (
        folded.e_a_star.mat.shape.0,
        folded.e_a_star.mat.shape.1,
        folded.a_star.mat.shape.1,
    );
    println!("Proved A* . lambda* = e_A*");
    let prover_proof_A_lambda_e_time = prover_proof_A_lambda_e_timer.elapsed().as_millis();

    let prover_proof_b_lambda_alpha_timer = Instant::now();
    let b_lambda_alpha_protocol = ZkMatMul::new(
        folded.alpha_star.blind_gt(srs),
        folded.b_star.blind,
        lambda_star_blind_gt,
        folded.alpha_star.mat.shape.0,
        folded.alpha_star.mat.shape.1,
        folded.b_star.mat.shape.1,
    );
    let mut b_lambda_alpha_prover = ZkTranSeqProver::new(srs);
    b_lambda_alpha_protocol.prove::<ZpElement, ZpElement, ZpElement>(
        srs,
        &mut b_lambda_alpha_prover,
        folded.alpha_star.mat.clone(),
        folded.b_star.mat.clone(),
        folded.lambda_star.mat.clone(),
        &folded.alpha_star.cache,
        &folded.b_star.cache,
        &folded.lambda_star.cache,
        folded.alpha_star.randomness,
        folded.b_star.randomness,
        folded.lambda_star.randomness,
    );
    let b_lambda_alpha_proof = b_lambda_alpha_prover.publish_trans();
    let b_lambda_alpha_dims = (
        folded.alpha_star.mat.shape.0,
        folded.alpha_star.mat.shape.1,
        folded.b_star.mat.shape.1,
    );
    println!("Proved b* . lambda* = alpha*");
    let prover_proof_b_lambda_alpha_time = prover_proof_b_lambda_alpha_timer.elapsed().as_millis();

    // ---- Public families: one batched zkmmeq (fig:ZKMC-S2-fold) ----
    // G* and h* are public, so they are folded in the clear and encoded into
    // the commitment bases; no commitment to either is ever formed.
    let prover_zkmmeq_timer = Instant::now();
    let zkmmeq_proof = zkmmeq::prove_g1(
        srs,
        &[folded.e_g_star.blind, folded.beta_star.blind],
        &[folded.e_g_star.randomness, folded.beta_star.randomness],
        &public_family_bases(srs, &folded.g_star, &folded.h_star),
        &mat_col_to_dense_zp(&folded.mu_star.mat),
        &zkmmeq::column_bases_g1(srs, folded.mu_star.mat.shape.0),
        folded.mu_star.blind,
        folded.mu_star.randomness,
    );
    println!("Proved G* . mu* = e_G* and h* . mu* = beta* (batched zkmmeq)");
    let prover_zkmmeq_time = prover_zkmmeq_timer.elapsed().as_millis();

    let total_zkmm_proving_time = prover_proof_A_lambda_e_time
        + prover_proof_b_lambda_alpha_time
        + prover_zkmmeq_time;

    // ---- ZKRP range proofs ----
    let prover_v_range_proof_timer = Instant::now();
    // NOTE: because we use alpha = -b.lambda (and similar for beta), we need
    // +ve values where the paper used -ve in v_i and r_v_i (except for 1,
    // that's still -1).
    let mut v_i: Vec<i64> = vec![0i64; N];
    v_i.par_iter_mut()
        .zip(input.alpha_i.par_iter())
        .zip(input.beta_i.par_iter())
        .for_each(|((v, alpha), beta)| {
            assert!(alpha.len() == 1 && alpha[0].len() == 1 && beta.len() == 1 && beta[0].len() == 1);
            *v = alpha[0][0] + beta[0][0] - 1;
        });
    let mut r_v_i: Vec<blstrs::Scalar> = vec![blstrs::Scalar::zero(); N];
    r_v_i
        .par_iter_mut()
        .zip(commitments.alpha.randomness.par_iter())
        .zip(commitments.beta.randomness.par_iter())
        .for_each(|((r_v, alpha_r), beta_r)| {
            *r_v = bls_field_elem_to_blstrs_scalar(&(*alpha_r + *beta_r));
        });
    println!("Calculated v_i, r_v_i, -r_v_i");

    // Mask commitments for the signed A_i / -b_i range proofs.
    // Proving A_i + M (resp. -b_i + M) in [0, 2M] shows A_i (resp. -b_i) in [-M, M].
    let (M_A_comm, _) = commit_rm_i64(
        &vec_mat_to_zkmatrix_i64("M_A".to_string(), &vec![vec![big_M as i64; max_n]; max_m]),
        srs,
    );
    let (M_b_comm, _) = commit_rm_i64(
        &vec_mat_to_zkmatrix_i64("M_b".to_string(), &vec![vec![big_M as i64; max_n]]),
        srs,
    );

    let mut bp_transcript = Transcript::new(b"v_range_proof");
    let mut v_i_padded = v_i.clone();
    v_i_padded.resize(params.aggregation_size, 0);
    let mut r_v_i_padded = r_v_i.clone();
    r_v_i_padded.resize(params.aggregation_size, blstrs::Scalar::zero());
    let neg_r_v_i_padded: Vec<blstrs::Scalar> = r_v_i_padded.par_iter().map(|r| -*r).collect();
    println!("Padded v_i, r_v_i, -r_v_i");
    let v_range_proof = ArbitraryUpperRangeProof::prove_multiple(
        &params.pc_gens,
        &params.bp_gens,
        &mut bp_transcript,
        &v_i_padded,
        big_M,
        &r_v_i_padded,
        &neg_r_v_i_padded,
        32,
    )
    .unwrap();
    let prover_v_range_proof_time = prover_v_range_proof_timer.elapsed().as_millis();
    println!("Proved v_i ranges");

    // zkrp checks a target-group commitment, so the column commitments are
    // paired up here -- N pairings, against the 4(N + |K|) target-group
    // scalar multiplications the G1 form removed from the fold.
    let lambda_blind_gt = commitments.lambda.blind_gt(srs);
    let mu_blind_gt = commitments.mu.blind_gt(srs);

    let prover_lambda_range_proofs_timer = Instant::now();
    let lambda_zkrp_proofs: Vec<zkrp::ZKRPProof> = (0..N)
        .into_par_iter()
        .map(|i| {
            let rho_i: Vec<blstrs::Scalar> = (0..input.lambda_i[i].len())
                .map(|_| bls_field_elem_to_blstrs_scalar(&ZpElement::rand()))
                .collect();
            zkrp::prove(
                &params.lambda_mu_zkrp_pp,
                &input.lambda_i[i],
                lambda_blind_gt[i],
                commitments.lambda.randomness[i],
                big_M,
                s_hat,
                &rho_i,
            )
        })
        .collect();
    let prover_lambda_range_proofs_time = prover_lambda_range_proofs_timer.elapsed().as_millis();
    println!("Proved lambda_i ranges");

    let prover_mu_range_proofs_timer = Instant::now();
    let mu_zkrp_proofs: Vec<zkrp::ZKRPProof> = (0..N)
        .into_par_iter()
        .map(|i| {
            let rho_i: Vec<blstrs::Scalar> = (0..input.mu_i[i].len())
                .map(|_| bls_field_elem_to_blstrs_scalar(&ZpElement::rand()))
                .collect();
            zkrp::prove(
                &params.lambda_mu_zkrp_pp,
                &input.mu_i[i],
                mu_blind_gt[i],
                commitments.mu.randomness[i],
                big_M,
                s_hat,
                &rho_i,
            )
        })
        .collect();
    let prover_mu_range_proofs_time = prover_mu_range_proofs_timer.elapsed().as_millis();
    println!("Proved mu_i ranges");

    let prover_A_range_proofs_timer = Instant::now();
    let A_zkrp_proofs: Vec<zkrp::ZKRPProof> = unique_A
        .par_iter()
        .enumerate()
        .map(|(u, A_u)| {
            let rho_i: Vec<blstrs::Scalar> = (0..max_m * max_n)
                .map(|_| bls_field_elem_to_blstrs_scalar(&ZpElement::rand()))
                .collect();
            // Shift A_i by +M so the (possibly negative) entries become non-negative
            // and provable in [0, 2M]; c_hat_v commits to A_i + M.
            let A_plus_M: Vec<Vec<i64>> = A_u
                .iter()
                .map(|row| row.iter().map(|&v| v + big_M as i64).collect())
                .collect();
            zkrp::prove(
                &params.A_zkrp_pp,
                &A_plus_M,
                unique_A_blinds[u] + M_A_comm,
                unique_A_randomness[u],
                2 * big_M,
                s_hat,
                &rho_i,
            )
        })
        .collect();
    let prover_A_range_proofs_time = prover_A_range_proofs_timer.elapsed().as_millis();
    println!("Proved {} unique A_i ranges", unique_A.len());

    let prover_b_range_proofs_timer = Instant::now();
    let b_zkrp_proofs: Vec<zkrp::ZKRPProof> = unique_b
        .par_iter()
        .enumerate()
        .map(|(u, neg_b_u)| {
            let rho_i: Vec<blstrs::Scalar> = (0..max_n)
                .map(|_| bls_field_elem_to_blstrs_scalar(&ZpElement::rand()))
                .collect();
            let neg_b_plus_M: Vec<Vec<i64>> = neg_b_u
                .iter()
                .map(|row| row.iter().map(|&v| v + big_M as i64).collect())
                .collect();
            zkrp::prove(
                &params.b_zkrp_pp,
                &neg_b_plus_M,
                unique_b_blinds[u] + M_b_comm,
                unique_b_randomness[u],
                2 * big_M,
                s_hat,
                &rho_i,
            )
        })
        .collect();
    let prover_b_range_proofs_time = prover_b_range_proofs_timer.elapsed().as_millis();
    println!("Proved {} unique -b_i ranges", unique_b.len());

    let total_proving_range_proofs_time = prover_v_range_proof_time
        + prover_lambda_range_proofs_time
        + prover_mu_range_proofs_time
        + prover_A_range_proofs_time
        + prover_b_range_proofs_time;

    // ---- Prover timing summary ----
    print_timing_ms("--Dedupe time:", dedupe_time);
    print_timing_ms("--Commitment time:", comm_time);
    print_timing_ms("--Prover folding time:", prover_folding_time);
    print_timing_ms("--Total ZKMM/zkmmeq proving time:", total_zkmm_proving_time);
    print_timing_ms("----A*.lambda* = e_A* time:", prover_proof_A_lambda_e_time);
    print_timing_ms("----b*.lambda* = alpha* time:", prover_proof_b_lambda_alpha_time);
    print_timing_ms("----zkmmeq (G*, h*) time:", prover_zkmmeq_time);
    print_timing_ms("--Total range proving time:", total_proving_range_proofs_time);
    print_timing_ms("----v_i range proof time:", prover_v_range_proof_time);
    print_timing_ms("----lambda_i range proof time:", prover_lambda_range_proofs_time);
    print_timing_ms("----mu_i range proof time:", prover_mu_range_proofs_time);
    print_timing_ms("----A_i range proof time:", prover_A_range_proofs_time);
    print_timing_ms("----b_i range proof time:", prover_b_range_proofs_time);

    let prover_total = dedupe_time
        + comm_time
        + prover_folding_time
        + total_zkmm_proving_time
        + total_proving_range_proofs_time;

    (
        ZKPProof {
            commitments,
            cross_a: folded.cross_a,
            cross_b: folded.cross_b,
            cross_g: folded.cross_g,
            cross_h: folded.cross_h,
            A_lambda_e_proof,
            b_lambda_alpha_proof,
            A_lambda_e_dims,
            b_lambda_alpha_dims,
            zkmmeq_proof,
            v_range_proof,
            lambda_zkrp_proofs,
            mu_zkrp_proofs,
            A_zkrp_proofs,
            b_zkrp_proofs,
            unique_A_blinds,
            unique_b_blinds,
        },
        prover_total,
    )
}

fn first_with_class(class_of: &[usize], class: usize) -> usize {
    class_of
        .iter()
        .position(|c| *c == class)
        .expect("class with no member")
}

/// Bases for the two public-family relations: G* and h* encoded into the
/// commitment bases of their outputs.
fn public_family_bases(
    srs: &SRS,
    g_star: &zkmatrix::mat::Mat<ZpElement>,
    h_star: &zkmatrix::mat::Mat<ZpElement>,
) -> Vec<Vec<G1Element>> {
    let output_bases = zkmmeq::column_bases_g1(srs, g_star.shape.0);
    vec![
        zkmmeq::derive_matrix_bases_g1(g_star, &output_bases),
        zkmmeq::derive_matrix_bases_g1(h_star, &output_bases[..h_star.shape.0]),
    ]
}

/// Commits the obligations, computing the row-major A / -b commitments once
/// per distinct matrix and expanding them across the obligations that share
/// them.
fn commit_obligations_deduped(
    unique_a: &[Vec<Vec<i64>>],
    obl_to_a_class: &[usize],
    unique_b: &[Vec<Vec<i64>>],
    obl_to_b_class: &[usize],
    lambda_i: &[Vec<Vec<i64>>],
    mu_i: &[Vec<Vec<i64>>],
    e_i: &[Vec<Vec<i64>>],
    alpha_i: &[Vec<Vec<i64>>],
    beta_i: &[Vec<Vec<i64>>],
    srs: &SRS,
    committer: &ColCommitter,
) -> ObligationCommitments {
    ObligationCommitments {
        a: commit_row_family_deduped("A", unique_a, obl_to_a_class, srs),
        neg_b: commit_row_family_deduped("-b", unique_b, obl_to_b_class, srs),
        lambda: commit_col_family(lambda_i, committer),
        mu: commit_col_family(mu_i, committer),
        e: commit_col_family(e_i, committer),
        alpha: commit_col_family(alpha_i, committer),
        beta: commit_col_family(beta_i, committer),
    }
}

impl ZKPProof {
    // Verifies the proof: folds the commitments itself, checks the two
    // secret-family ZKMM relations and the batched public zkmmeq, and
    // verifies all the range proofs. Uses only the public statement
    // (neg_G_i, neg_h_i) plus the proof; it recomputes the public mask
    // commitments itself rather than trusting the prover's.
    // Returns whether everything verified plus the total verifier time in ms.
    pub fn verify(&self, params: &ZKPParams, statement: &ZKPStatement) -> (bool, u128) {
        let N = params.N;
        let max_m = params.max_m;
        let max_n = params.max_n;
        let big_M = params.big_M;
        let srs = &params.srs;

        let ctx = FoldContext {
            srs,
            schedule: &params.schedule,
            pairs: &params.pairs,
            committer: &params.committer,
        };

        // ---- Verifier Fold ----
        let verifier_fold_timer = Instant::now();
        let folded = verifier_fold(
            &self.commitments,
            &self.cross_a,
            &self.cross_b,
            &self.cross_g,
            &self.cross_h,
            &statement.neg_G_i,
            &statement.neg_h_i,
            &ctx,
        );
        let verifier_fold_time = verifier_fold_timer.elapsed().as_millis();

        // ---- Secret families ----
        let lambda_star_blind_gt = col_comm_to_gt(folded.lambda_star_blind, srs);

        let verifier_proof_A_lambda_e_timer = Instant::now();
        let A_lambda_e_verifier = ZkMatMul::new(
            col_comm_to_gt(folded.e_a_star_blind, srs),
            folded.a_star_blind,
            lambda_star_blind_gt,
            self.A_lambda_e_dims.0,
            self.A_lambda_e_dims.1,
            self.A_lambda_e_dims.2,
        );
        let A_lambda_e_verified =
            A_lambda_e_verifier.verify(srs, &mut self.A_lambda_e_proof.clone());
        println!("A* . lambda* = e_A* verified: {A_lambda_e_verified}");
        let verifier_proof_A_lambda_e_time = verifier_proof_A_lambda_e_timer.elapsed().as_millis();

        let verifier_proof_b_lambda_alpha_timer = Instant::now();
        let b_lambda_alpha_verifier = ZkMatMul::new(
            col_comm_to_gt(folded.alpha_star_blind, srs),
            folded.b_star_blind,
            lambda_star_blind_gt,
            self.b_lambda_alpha_dims.0,
            self.b_lambda_alpha_dims.1,
            self.b_lambda_alpha_dims.2,
        );
        let b_lambda_alpha_verified =
            b_lambda_alpha_verifier.verify(srs, &mut self.b_lambda_alpha_proof.clone());
        println!("b* . lambda* = alpha* verified: {b_lambda_alpha_verified}");
        let verifier_proof_b_lambda_alpha_time =
            verifier_proof_b_lambda_alpha_timer.elapsed().as_millis();

        // ---- Public families ----
        let verifier_zkmmeq_timer = Instant::now();
        let zkmmeq_verified = self.zkmmeq_proof.verify(
            srs,
            &[folded.e_g_star_blind, folded.beta_star_blind],
            &public_family_bases(srs, &folded.g_star, &folded.h_star),
            &zkmmeq::column_bases_g1(srs, max_n),
            folded.mu_star_blind,
        );
        println!("G* . mu* = e_G* and h* . mu* = beta* verified: {zkmmeq_verified}");
        let verifier_zkmmeq_time = verifier_zkmmeq_timer.elapsed().as_millis();

        let total_zkmm_verifying_time = verifier_proof_A_lambda_e_time
            + verifier_proof_b_lambda_alpha_time
            + verifier_zkmmeq_time;

        // ---- Range proof verification ----
        let verifier_v_range_proof_verify_timer = Instant::now();
        let mut verif_transcript = Transcript::new(b"v_range_proof");
        let v_range_proof_verified = self
            .v_range_proof
            .verify_multiple(&params.pc_gens, &params.bp_gens, &mut verif_transcript, 32)
            .unwrap();
        println!("v_i range proof verified: {v_range_proof_verified}");
        let verifier_v_range_proof_verify_time =
            verifier_v_range_proof_verify_timer.elapsed().as_millis();

        let lambda_blind_gt = self.commitments.lambda.blind_gt(srs);
        let mu_blind_gt = self.commitments.mu.blind_gt(srs);

        let verifier_lambda_range_proofs_verify_timer = Instant::now();
        let lambda_ranges_ok = (0..N).into_par_iter().all(|i| {
            self.lambda_zkrp_proofs[i].verify(&params.lambda_mu_zkrp_pp, lambda_blind_gt[i], big_M)
        });
        println!("lambda_i range proofs verified: {lambda_ranges_ok}");
        let verifier_lambda_range_proofs_verify_time =
            verifier_lambda_range_proofs_verify_timer.elapsed().as_millis();

        let verifier_mu_range_proofs_verify_timer = Instant::now();
        let mu_ranges_ok = (0..N).into_par_iter().all(|i| {
            self.mu_zkrp_proofs[i].verify(&params.lambda_mu_zkrp_pp, mu_blind_gt[i], big_M)
        });
        println!("mu_i range proofs verified: {mu_ranges_ok}");
        let verifier_mu_range_proofs_verify_time =
            verifier_mu_range_proofs_verify_timer.elapsed().as_millis();

        // Verifier independently recomputes the public mask commitments.
        let (verifier_M_A_comm, _) = commit_rm_i64(
            &vec_mat_to_zkmatrix_i64("M_A".to_string(), &vec![vec![big_M as i64; max_n]; max_m]),
            srs,
        );
        let (verifier_M_b_comm, _) = commit_rm_i64(
            &vec_mat_to_zkmatrix_i64("M_b".to_string(), &vec![vec![big_M as i64; max_n]]),
            srs,
        );

        let verifier_A_range_proofs_verify_timer = Instant::now();
        let A_ranges_ok = (0..self.unique_A_blinds.len()).into_par_iter().all(|u| {
            self.A_zkrp_proofs[u].verify(
                &params.A_zkrp_pp,
                self.unique_A_blinds[u] + verifier_M_A_comm,
                2 * big_M,
            )
        });
        println!(
            "A_i range proofs verified ({} unique): {A_ranges_ok}",
            self.unique_A_blinds.len()
        );
        let verifier_A_range_proofs_verify_time =
            verifier_A_range_proofs_verify_timer.elapsed().as_millis();

        let verifier_b_range_proofs_verify_timer = Instant::now();
        let b_ranges_ok = (0..self.unique_b_blinds.len()).into_par_iter().all(|u| {
            self.b_zkrp_proofs[u].verify(
                &params.b_zkrp_pp,
                self.unique_b_blinds[u] + verifier_M_b_comm,
                2 * big_M,
            )
        });
        println!(
            "-b_i range proofs verified ({} unique): {b_ranges_ok}",
            self.unique_b_blinds.len()
        );
        let verifier_b_range_proofs_verify_time =
            verifier_b_range_proofs_verify_timer.elapsed().as_millis();

        // The per-obligation A / -b commitments must be the canonical ones the
        // range proofs were made against, otherwise a prover could range-prove
        // one matrix and fold another.
        let canonical_ok = self.canonical_row_commitments_ok();
        if !canonical_ok {
            println!("A_i / -b_i commitments are not among the range-proved canonical ones");
        }

        let total_verifying_range_proofs_time = verifier_v_range_proof_verify_time
            + verifier_lambda_range_proofs_verify_time
            + verifier_mu_range_proofs_verify_time
            + verifier_A_range_proofs_verify_time
            + verifier_b_range_proofs_verify_time;

        // ---- Verifier timing summary ----
        print_timing_ms("--Verifier folding time:", verifier_fold_time);
        print_timing_ms("--Total ZKMM/zkmmeq verifying time:", total_zkmm_verifying_time);
        print_timing_ms("----A*.lambda* = e_A* time:", verifier_proof_A_lambda_e_time);
        print_timing_ms("----b*.lambda* = alpha* time:", verifier_proof_b_lambda_alpha_time);
        print_timing_ms("----zkmmeq (G*, h*) time:", verifier_zkmmeq_time);
        print_timing_ms("--Total range verifying time:", total_verifying_range_proofs_time);
        print_timing_ms("----v_i range verify time:", verifier_v_range_proof_verify_time);
        print_timing_ms(
            "----lambda_i range verify time:",
            verifier_lambda_range_proofs_verify_time,
        );
        print_timing_ms("----mu_i range verify time:", verifier_mu_range_proofs_verify_time);
        print_timing_ms("----A_i range verify time:", verifier_A_range_proofs_verify_time);
        print_timing_ms("----b_i range verify time:", verifier_b_range_proofs_verify_time);

        let verifier_total =
            verifier_fold_time + total_zkmm_verifying_time + total_verifying_range_proofs_time;

        let ok = A_lambda_e_verified
            && b_lambda_alpha_verified
            && zkmmeq_verified
            && v_range_proof_verified
            && lambda_ranges_ok
            && mu_ranges_ok
            && A_ranges_ok
            && b_ranges_ok
            && canonical_ok;

        (ok, verifier_total)
    }

    /// Every per-obligation A / -b commitment must equal one of the canonical
    /// blinds carrying a range proof.
    fn canonical_row_commitments_ok(&self) -> bool {
        let a_ok = self
            .commitments
            .a
            .blind
            .par_iter()
            .all(|c| self.unique_A_blinds.contains(c));
        let b_ok = self
            .commitments
            .neg_b
            .blind
            .par_iter()
            .all(|c| self.unique_b_blinds.contains(c));
        a_ok && b_ok
    }
}

pub fn print_timing(label: &str, time_ms: u128) {
    println!("{label:<70}{time_ms:>15}ms");
}

fn print_timing_ms(label: &str, time_ms: u128) {
    print_timing(label, time_ms);
}
