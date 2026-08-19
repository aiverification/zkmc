use crate::utils::*;
use crate::zkmmeq;
use crate::zkmmeq::EqualProof;
use crate::zkrp;
use crate::zkrp::ZKRPProof;
use bls_bulletproofs::{BulletproofGens, PedersenGens};
use blstrs;
use zkmatrix::utils::curve as bls;
use zkmatrix::utils::curve::{G1Element, G2Element, GtElement, ZpElement};
use zkmatrix::utils::fiat_shamir::TranSeq;

use zkmatrix::{
    commit_mat::CommitMat,
    zkprotocols::{zk_matmul::ZkMatMul, zk_trans::ZkTranSeqProver},
};

#[derive(Clone)]
pub struct ZkMatMulDimsCached {
    pub m: usize,
    pub n: usize,
    pub l: usize,
}

impl ZkMatMulDimsCached {
    pub fn new(a: &Vec<Vec<i64>>, b: &Vec<Vec<i64>>, c: &Vec<Vec<i64>>) -> Self {
        let a_mat = vec_mat_to_zkmatrix_i64("a".to_string(), a);
        let c_mat = vec_mat_to_zkmatrix_i128("c".to_string(), c);
        return ZkMatMulDimsCached {
            m: c_mat.shape.0,
            n: c_mat.shape.1,
            l: a_mat.shape.1,
        };
    }
}

#[derive(Clone)]
pub struct ZkpSRSCached {
    pub m: usize,
    pub n: usize,
    pub n_prime: usize,
    pub l: usize,
    pub q: usize,
    pub big_M: u32,
    pub g_blstrs: blstrs::G1Affine,
    pub g_bls: G1Element,
    pub g_prime: bls::G2Element,
    pub g_prime_alpha: G2Element,
    pub g_i_vec: Vec<G1Element>,
    pub h: G1Element,
    pub h_prime: G2Element,
    pub g_hat_mat: Vec<Vec<GtElement>>,
    pub zk_matrix_srs: zkmatrix::setup::SRS,
    pub pc_gens: PedersenGens,
    pub bp_gens: BulletproofGens,
    pub A_lambda_e1_dims: ZkMatMulDimsCached,
    pub b_lambda_e2_dims: ZkMatMulDimsCached,
}

pub struct CachedProverPieces {
    // For A+M caching
    pub A_r: ZpElement,
    pub A_blind: GtElement,
    pub A_plus_M_zkrp: ZKRPProof,
    // For -b+M caching
    pub b_r: ZpElement,
    pub b_blind: GtElement,
    pub neg_b_plus_M_zkrp: ZKRPProof,
    // For mu caching
    pub mu_s: Vec<Vec<i64>>,
    pub mu_r: ZpElement,
    pub mu_blind: GtElement,
    pub mu_zkrp: ZKRPProof,
    // For lambda caching
    pub lambda_r: ZpElement,
    pub lambda_blind: GtElement,
    pub lambda_zkrp: ZKRPProof,
    // For A.lambda = e1 caching
    pub e1_r: ZpElement,
    pub e1_blind: GtElement,
    pub e1_zkmm: TranSeq,
    // For -b.lambda = e2 caching
    pub e2_scalar: i64,
    pub e2_r: ZpElement,
    pub e2_blind: GtElement,
    pub e2_zkmm: TranSeq,
    // For -h.mu = e3 caching
    pub e3_scalar: i64,
    pub e3_blind: GtElement,
    pub h_r: ZpElement,
}

pub struct ZkpProofCached {
    pub c_A: GtElement,
    pub c_b: GtElement,
    pub c_lambda: GtElement,
    pub c_mu: GtElement,
    pub c_e_1: GtElement,
    pub c_e_2: GtElement,
    pub c_e_3: GtElement,
    pub A_lambda_e1_proof: TranSeq,
    pub b_lambda_e2_proof: TranSeq,
    pub equal_proof: EqualProof,
    pub lambda_zkrp_proof: ZKRPProof,
    pub mu_zkrp_proof: ZKRPProof,
    pub A_plus_M_zkrp_proof: ZKRPProof,
    pub neg_b_plus_M_zkrp_proof: ZKRPProof,
    pub b_lambda_h_mu_one_zkrp_proof: ZKRPProof,
}

pub struct VerifierCacheFlagsCached {
    pub is_A_plus_M_cached: bool,
    pub is_neg_b_plus_M_cached: bool,
    pub is_lambda_cached: bool,
    pub is_mu_cached: bool,
    pub is_e1_cached: bool,
    pub is_e2_cached: bool,
}

#[derive(Clone)]
pub struct PrecomputedFixedComms {
    pub M_m_n_comm: GtElement,
    pub M_1_n_comm: GtElement,
    pub neg_one_comm: GtElement,
}

#[derive(Clone)]
pub struct AVerifyEntry {
    pub c_A: GtElement,
    pub A_plus_M_zkrp: ZKRPProof,
    pub m: usize,
    pub n: usize,
}

#[derive(Clone)]
pub struct BVerifyEntry {
    pub c_b: GtElement,
    pub neg_b_plus_M_zkrp: ZKRPProof,
    pub n: usize,
}

#[derive(Clone)]
pub struct ZKRPVerifyEntry {
    pub commitment: GtElement,
    pub zkrp_proof: ZKRPProof,
    pub m: usize,
    pub n: usize,
    pub l: usize,
}

#[derive(Clone)]
pub struct ZkmmVerifyEntry {
    pub c_a: GtElement,
    pub c_e: GtElement,
    pub c_lambda: GtElement,
    pub proof: TranSeq,
    pub m: usize,
    pub n: usize,
    pub l: usize,
}

pub fn prove(
    pp: &ZkpSRSCached,
    A_s_T: &Vec<Vec<i64>>,
    neg_b_s_T: &Vec<Vec<i64>>,
    lambda_s: &Vec<Vec<i64>>,
    mu_s: &Vec<Vec<i64>>,
    G_p_T: &Vec<Vec<i64>>,
    h_p_T: &Vec<Vec<i64>>,
    e_1: &Vec<Vec<i64>>,
    e_2: &Vec<Vec<i64>>,
    e_3: &Vec<Vec<i64>>,
    comm_A_s_T: Option<GtElement>,
    cache_A_s_T: Option<Vec<G2Element>>,
    rand_A_s_T: Option<ZpElement>,
    blind_A_s_T: Option<GtElement>,
    comm_neg_b_s_T: Option<GtElement>,
    cache_neg_b_s_T: Option<Vec<G2Element>>,
    rand_neg_b_s_T: Option<ZpElement>,
    blind_neg_b_s_T: Option<GtElement>,
    A_plus_M_zkrp_proof_cached: Option<ZKRPProof>,
    neg_b_plus_M_zkrp_proof_cached: Option<ZKRPProof>,
    alpha: ZpElement,
) -> ZkpProofCached {
    assert!(
        A_s_T.len() == pp.m && G_p_T.len() == pp.m,
        "Error: invalid m"
    );
    assert!(
        A_s_T[0].len() == pp.n && G_p_T[0].len() == pp.n_prime,
        "Error: invalid n or n'"
    );
    //TODO - add assertions for equal length rows + columns? or is vec_to_mat handling that?
    //TODO - add checks for lengths of other vectors/matrices passed

    //Convert matrices to CommitMat
    let mat_A = vec_mat_to_zkmatrix_i64("A_s^T".to_string(), A_s_T);
    let mat_b = vec_mat_to_zkmatrix_i64("-b_s^T".to_string(), neg_b_s_T);
    let mat_lambda = vec_mat_to_zkmatrix_i64("lambda_s".to_string(), lambda_s);
    let mat_mu = vec_mat_to_zkmatrix_i64("mu_s".to_string(), mu_s);
    let mat_e_1 = vec_mat_to_zkmatrix_i128("e_1".to_string(), e_1);
    let mat_e_2 = vec_mat_to_zkmatrix_i128("e_2".to_string(), e_2);
    let mat_e_3 = vec_mat_to_zkmatrix_i128("e_3".to_string(), e_3);

    //Handle user-given commitments, else create them
    let A_comm: GtElement;
    let A_cache: Vec<G2Element>;
    let A_r: ZpElement;
    let A_blind: GtElement;
    if comm_A_s_T.is_some()
        && cache_A_s_T.is_some()
        && rand_A_s_T.is_some()
        && blind_A_s_T.is_some()
    {
        A_comm = comm_A_s_T.unwrap();
        A_cache = cache_A_s_T.unwrap().clone();
        A_r = rand_A_s_T.unwrap();
        A_blind = blind_A_s_T.unwrap();
        assert!(A_blind == A_comm + (A_r * pp.zk_matrix_srs.blind_base));
    } else {
        A_r = ZpElement::rand();
        (A_comm, A_cache) = mat_A.commit_rm(&pp.zk_matrix_srs);
        A_blind = A_comm + (A_r * pp.zk_matrix_srs.blind_base);
    }

    let b_comm: GtElement;
    let b_cache: Vec<G2Element>;
    let b_r: ZpElement;
    let b_blind: GtElement;
    if comm_neg_b_s_T.is_some()
        && cache_neg_b_s_T.is_some()
        && rand_neg_b_s_T.is_some()
        && blind_neg_b_s_T.is_some()
    {
        b_comm = comm_neg_b_s_T.unwrap();
        b_cache = cache_neg_b_s_T.unwrap().clone();
        b_r = rand_neg_b_s_T.unwrap();
        b_blind = blind_neg_b_s_T.unwrap();
        assert!(b_blind == b_comm + (b_r * pp.zk_matrix_srs.blind_base));
    } else {
        b_r = ZpElement::rand();
        (b_comm, b_cache) = mat_b.commit_rm(&pp.zk_matrix_srs);
        b_blind = b_comm + (b_r * pp.zk_matrix_srs.blind_base);
    }

    let mut M_m_n: Vec<Vec<i64>> = vec![];
    for _ in 0..pp.m {
        M_m_n.push(vec![pp.big_M as i64; pp.n]);
    }
    let mat_M_m_n = vec_mat_to_zkmatrix_i64("M_m_n".to_string(), &M_m_n);
    let (M_m_n_comm, _) = mat_M_m_n.commit_rm(&pp.zk_matrix_srs);
    let A_prime_comm = A_blind + M_m_n_comm;

    let mut M_1_n: Vec<Vec<i64>> = vec![vec![pp.big_M as i64; pp.n]];
    let mat_M_1_n = vec_mat_to_zkmatrix_i64("M_1_n".to_string(), &M_1_n);
    let (M_1_n_comm, _) = mat_M_1_n.commit_rm(&pp.zk_matrix_srs);
    let b_prime_comm = b_blind + M_1_n_comm;

    let lambda_r = ZpElement::rand();
    let (lambda_comm, lambda_cache) = mat_lambda.commit_cm(&pp.zk_matrix_srs);
    let lambda_blind = lambda_comm + (lambda_r * pp.zk_matrix_srs.blind_base);

    let mu_r = ZpElement::rand();
    let (mu_comm, _) = mat_mu.commit_cm(&pp.zk_matrix_srs);
    let mu_blind = mu_comm + (mu_r * pp.zk_matrix_srs.blind_base);

    let e_1_r = A_r + lambda_r;
    let e_2_r = b_r + lambda_r;
    let h_r = ZpElement::rand();
    let e_3_r = h_r + mu_r;

    let (e_1_comm, e_1_cache) = mat_e_1.commit_cm(&pp.zk_matrix_srs);
    let e_1_blind = e_1_comm + (e_1_r * pp.zk_matrix_srs.blind_base);

    let (e_2_comm, e_2_cache) = mat_e_2.commit_cm(&pp.zk_matrix_srs);
    let e_2_blind = e_2_comm + (e_2_r * pp.zk_matrix_srs.blind_base);

    let (e_3_comm, _) = mat_e_3.commit_cm(&pp.zk_matrix_srs);
    let e_3_blind = e_3_comm + (e_3_r * pp.zk_matrix_srs.blind_base);

    let neg_one = vec![vec![-1i64]];
    let mat_neg_one = vec_mat_to_zkmatrix_i128("-1".to_string(), &neg_one);
    let (neg_one_comm, _) = mat_neg_one.commit_cm(&pp.zk_matrix_srs);

    let c_u = e_2_blind + e_3_blind + neg_one_comm;
    let c_u_r = e_2_r + e_3_r;

    //Prove A_s^T . lambda_s = e_1
    // println!("proving A.lambda=e1");
    let A_lambda_e1_protocol = ZkMatMul::new(
        e_1_blind,
        A_blind,
        lambda_blind,
        pp.A_lambda_e1_dims.m,
        pp.A_lambda_e1_dims.n,
        pp.A_lambda_e1_dims.l,
    );
    let mut A_lambda_e1_prover = ZkTranSeqProver::new(&pp.zk_matrix_srs);
    A_lambda_e1_protocol.prove::<i128, i64, i64>(
        &pp.zk_matrix_srs,
        &mut A_lambda_e1_prover,
        mat_e_1.clone(),
        mat_A.clone(),
        mat_lambda.clone(),
        &e_1_cache,
        &A_cache,
        &lambda_cache,
        e_1_r,
        A_r,
        lambda_r,
    );
    let A_lambda_e1_proof = A_lambda_e1_prover.publish_trans();

    //Prove -b_s^T . lambda_s = e_2
    // println!("proving -b.lambda=e2");
    let mut b_lambda_e2_prover = ZkTranSeqProver::new(&pp.zk_matrix_srs);
    let b_lambda_e2_protocol = ZkMatMul::new(
        e_2_blind,
        b_blind,
        lambda_blind,
        pp.b_lambda_e2_dims.m,
        pp.b_lambda_e2_dims.n,
        pp.b_lambda_e2_dims.l,
    );
    b_lambda_e2_protocol.prove::<i128, i64, i64>(
        &pp.zk_matrix_srs,
        &mut b_lambda_e2_prover,
        mat_e_2.clone(),
        mat_b.clone(),
        mat_lambda.clone(),
        &e_2_cache,
        &b_cache,
        &lambda_cache,
        e_2_r,
        b_r,
        lambda_r,
    );
    let b_lambda_e2_proof = b_lambda_e2_prover.publish_trans();

    //Equal proof
    // println!("proving equals");
    let mu_T = transpose_matrix(&mu_s);
    let mut mu_g_hat_i: Vec<GtElement> = Vec::with_capacity(mu_T.len() * mu_T[0].len());
    for j in 0..mu_s.len() {
        for k in 0..mu_s[j].len() {
            mu_g_hat_i.push(pp.g_hat_mat[j][k].clone());
        }
    }
    let mut first_basis_vec: Vec<GtElement> = Vec::with_capacity(pp.n_prime);
    let mut second_basis_vec: Vec<GtElement> = Vec::with_capacity(pp.n_prime);
    let mut third_basis_vec: Vec<GtElement> = Vec::with_capacity(pp.n_prime);
    for k in 0..pp.n_prime {
        let mut first_basis_prod = get_bls_gt_zero();
        for j in 0..pp.m {
            first_basis_prod += (pp.g_hat_mat[j][0] * (-G_p_T[j][k]));
        }
        first_basis_vec.push(first_basis_prod);
        second_basis_vec.push(pp.g_hat_mat[0][0] * (-h_p_T[0][k]));
        third_basis_vec.push(pp.g_hat_mat[k][0])
    }
    let mu_T_flat: Vec<ZpElement> = mu_T[0].iter().map(|m| ZpElement::from(*m as u64)).collect(); // m > 0 so this is fine
    let equal_proof = zkmmeq::prove(
    &pp.zk_matrix_srs, 
    &vec![e_1_blind, e_3_blind],
    &vec![e_1_r, e_3_r],
    &vec![&first_basis_vec, &second_basis_vec],
    &mu_T_flat,
    &third_basis_vec,
    mu_blind,
    mu_r,
    );

    //ZKRPs
    //First, lambda
    // println!("proving lambda ZKRP");
    let mut lambda_g_hat_i: Vec<GtElement> =
        Vec::with_capacity(mat_lambda.shape.0 * mat_lambda.shape.1);
    for j in 0..mat_lambda.shape.0 {
        for k in 0..mat_lambda.shape.1 {
            lambda_g_hat_i.push(pp.g_hat_mat[j][k].clone());
        }
    }
    let lambda_l = mat_lambda.shape.0 * mat_lambda.shape.1;
    let mut lambda_g_i = pp.g_i_vec[0..2 * lambda_l].to_vec();
    lambda_g_i[lambda_l] = get_bls_g1_zero();
    let lambda_zkrp_pp = zkrp::ZKRPParams {
        l: lambda_l,
        m: mat_lambda.shape.0,
        n: mat_lambda.shape.1,
        g_blstrs: pp.g_blstrs,
        g_bls: pp.g_bls,
        g_prime: pp.g_prime,
        g_prime_alpha: pp.g_prime_alpha,
        g_i: pp.g_i_vec.clone(),
        h: pp.h,
        h_prime: pp.h_prime,
        zk_matrix_srs: pp.zk_matrix_srs.clone(),
        pc_gens: pp.pc_gens.clone(),
        bp_gens: pp.bp_gens.clone(),
    };
    let lambda_zkrp_proof = zkrp::prove(
        &lambda_zkrp_pp,
        lambda_s,
        lambda_blind,
        lambda_r,
        pp.big_M,
        alpha,
    );

    //Second, mu
    // println!("proving mu ZKRP");
    let mut mu_g_hat_i: Vec<GtElement> = Vec::with_capacity(mat_mu.shape.0 * mat_mu.shape.1);
    for j in 0..mat_mu.shape.0 {
        for k in 0..mat_mu.shape.1 {
            mu_g_hat_i.push(pp.g_hat_mat[j][k].clone());
        }
    }
    let mu_l = mat_mu.shape.0 * mat_mu.shape.1;
    let mut mu_g_i = pp.g_i_vec[0..2 * mu_l].to_vec();
    mu_g_i[mu_l] = get_bls_g1_zero();
    let mu_zkrp_pp = zkrp::ZKRPParams {
        l: mu_l,
        m: mat_mu.shape.0,
        n: mat_mu.shape.1,
        g_blstrs: pp.g_blstrs,
        g_bls: pp.g_bls,
        g_prime: pp.g_prime,
        g_prime_alpha: pp.g_prime_alpha,
        g_i: pp.g_i_vec.clone(),
        h: pp.h,
        h_prime: pp.h_prime,
        zk_matrix_srs: pp.zk_matrix_srs.clone(),
        pc_gens: pp.pc_gens.clone(),
        bp_gens: pp.bp_gens.clone(),
    };
    let mu_zkrp_proof = zkrp::prove(&mu_zkrp_pp, mu_s, mu_blind, mu_r, pp.big_M, alpha);

    //Now A_s^T + M^mxn
    // println!("proving A+M ZKRP");
    let mut A_plus_M_zkrp_proof: zkrp::ZKRPProof;
    if A_plus_M_zkrp_proof_cached.is_some() {
        A_plus_M_zkrp_proof = A_plus_M_zkrp_proof_cached.unwrap();
    } else {
        let mut A_plus_M: Vec<Vec<i64>> = vec![vec![pp.big_M as i64; pp.n]; pp.m];
        let mut A_plus_M_g_hat_i: Vec<GtElement> = Vec::with_capacity(pp.n * pp.m);
        for j in 0..pp.m {
            for k in 0..pp.n {
                A_plus_M[j][k] += A_s_T[j][k];
                A_plus_M_g_hat_i.push(pp.g_hat_mat[j][k].clone());
            }
        }
        let mat_A_plus_M = vec_mat_to_zkmatrix_i64("A+M^mxn".to_string(), &A_plus_M);
        let A_plus_M_l = mat_A_plus_M.shape.0 * mat_A_plus_M.shape.1;
        let mut A_plus_M_g_i = pp.g_i_vec[0..2 * A_plus_M_l].to_vec();
        A_plus_M_g_i[A_plus_M_l] = get_bls_g1_zero();
        let A_plus_M_zkrp_pp = zkrp::ZKRPParams {
            l: A_plus_M_l,
            m: mat_A_plus_M.shape.0,
            n: mat_A_plus_M.shape.1,
            g_blstrs: pp.g_blstrs,
            g_bls: pp.g_bls,
            g_prime: pp.g_prime,
            g_prime_alpha: pp.g_prime_alpha,
            g_i: pp.g_i_vec.clone(),
            h: pp.h,
            h_prime: pp.h_prime,
            zk_matrix_srs: pp.zk_matrix_srs.clone(),
            pc_gens: pp.pc_gens.clone(),
            bp_gens: pp.bp_gens.clone(),
        };
        A_plus_M_zkrp_proof = zkrp::prove(
            &A_plus_M_zkrp_pp,
            &A_plus_M,
            A_prime_comm,
            A_r,
            2 * pp.big_M,
            alpha,
        );
    }

    //Now -b_s^T + M^1xn
    // println!("proving -b+M ZKRP");
    let mut neg_b_plus_M_zkrp_proof: zkrp::ZKRPProof;
    if neg_b_plus_M_zkrp_proof_cached.is_some() {
        neg_b_plus_M_zkrp_proof = neg_b_plus_M_zkrp_proof_cached.unwrap();
    } else {
        let mut neg_b_plus_M: Vec<Vec<i64>> = vec![vec![pp.big_M as i64; pp.n]];
        let mut neg_b_plus_M_g_hat_i: Vec<GtElement> = Vec::with_capacity(pp.n);
        for j in 0..1usize {
            for k in 0..pp.n {
                neg_b_plus_M[j][k] += neg_b_s_T[j][k];
                neg_b_plus_M_g_hat_i.push(pp.g_hat_mat[j][k].clone());
            }
        }
        let mat_neg_b_plus_M = vec_mat_to_zkmatrix_i64("-b+M^1xn".to_string(), &neg_b_plus_M);
        let neg_b_plus_M_l = mat_neg_b_plus_M.shape.0 * mat_neg_b_plus_M.shape.1;
        let mut neg_b_plus_M_g_i = pp.g_i_vec[0..2 * neg_b_plus_M_l].to_vec();
        neg_b_plus_M_g_i[neg_b_plus_M_l] = get_bls_g1_zero();
        let neg_b_plus_M_zkrp_pp = zkrp::ZKRPParams {
            l: neg_b_plus_M_l,
            m: mat_neg_b_plus_M.shape.0,
            n: mat_neg_b_plus_M.shape.1,
            g_blstrs: pp.g_blstrs,
            g_bls: pp.g_bls,
            g_prime: pp.g_prime,
            g_prime_alpha: pp.g_prime_alpha,
            g_i: pp.g_i_vec.clone(),
            h: pp.h,
            h_prime: pp.h_prime,
            zk_matrix_srs: pp.zk_matrix_srs.clone(),
            pc_gens: pp.pc_gens.clone(),
            bp_gens: pp.bp_gens.clone(),
        };
        neg_b_plus_M_zkrp_proof = zkrp::prove(
            &neg_b_plus_M_zkrp_pp,
            &neg_b_plus_M,
            b_prime_comm,
            b_r,
            2 * pp.big_M,
            alpha,
        );
    }

    //Finally, -b_s^T.lambda_s - h_p_T^T.mu_s - 1
    // println!("proving -b.lambda - h.mu - 1 ZKRP");
    let b_lambda_h_mu_one: Vec<Vec<i64>> = vec![vec![e_2[0][0] + e_3[0][0] - 1]];
    let mat_b_lambda_h_mu_one =
        vec_mat_to_zkmatrix_i64("-b.lambda - h.mu - 1".to_string(), &b_lambda_h_mu_one);
    let b_lambda_h_mu_one_l = mat_b_lambda_h_mu_one.shape.0 * mat_b_lambda_h_mu_one.shape.1;
    let mut b_lambda_h_mu_one_g_i = pp.g_i_vec[0..2 * b_lambda_h_mu_one_l].to_vec();
    b_lambda_h_mu_one_g_i[b_lambda_h_mu_one_l] = get_bls_g1_zero();
    let b_lambda_h_mu_one_zkrp_pp = zkrp::ZKRPParams {
        l: b_lambda_h_mu_one_l,
        m: mat_b_lambda_h_mu_one.shape.0,
        n: mat_b_lambda_h_mu_one.shape.1,
        g_blstrs: pp.g_blstrs,
        g_bls: pp.g_bls,
        g_prime: pp.g_prime,
        g_prime_alpha: pp.g_prime_alpha,
        g_i: pp.g_i_vec.clone(),
        h: pp.h,
        h_prime: pp.h_prime,
        zk_matrix_srs: pp.zk_matrix_srs.clone(),
        pc_gens: pp.pc_gens.clone(),
        bp_gens: pp.bp_gens.clone(),
    };
    let b_lambda_h_mu_one_zkrp_proof = zkrp::prove(
        &b_lambda_h_mu_one_zkrp_pp,
        &b_lambda_h_mu_one,
        c_u,
        c_u_r,
        pp.big_M,
        alpha,
    );

    return ZkpProofCached {
        c_A: A_blind,
        c_b: b_blind,
        c_lambda: lambda_blind,
        c_mu: mu_blind,
        c_e_1: e_1_blind,
        c_e_2: e_2_blind,
        c_e_3: e_3_blind,
        A_lambda_e1_proof: A_lambda_e1_proof.clone(),
        b_lambda_e2_proof: b_lambda_e2_proof.clone(),
        equal_proof,
        lambda_zkrp_proof,
        mu_zkrp_proof,
        A_plus_M_zkrp_proof,
        neg_b_plus_M_zkrp_proof,
        b_lambda_h_mu_one_zkrp_proof,
    };
}

pub fn prove_cached(
    pp: &ZkpSRSCached,
    G_p_T: &Vec<Vec<i64>>,
    h_p_T: &Vec<Vec<i64>>,
    cached: &CachedProverPieces,
    alpha: ZpElement,
) -> ZkpProofCached {
    debug_assert!(
        cached.e1_r == cached.A_r + cached.lambda_r,
        "e1_r must equal A_r + lambda_r"
    );
    debug_assert!(
        cached.e2_r == cached.b_r + cached.lambda_r,
        "e2_r must equal b_r + lambda_r"
    );
    let e_3_r = cached.h_r + cached.mu_r;

    let neg_one = vec![vec![-1i64]];
    let mat_neg_one = vec_mat_to_zkmatrix_i128("-1".to_string(), &neg_one);
    let (neg_one_comm, _) = mat_neg_one.commit_cm(&pp.zk_matrix_srs);

    let c_u = cached.e2_blind + cached.e3_blind + neg_one_comm;
    let c_u_r = cached.e2_r + e_3_r;

    // Equals proof
    // println!("proving equals (cached)");
    let mu_T = transpose_matrix(&cached.mu_s);
    let mut first_basis_vec: Vec<GtElement> = Vec::with_capacity(pp.n_prime);
    let mut second_basis_vec: Vec<GtElement> = Vec::with_capacity(pp.n_prime);
    let mut third_basis_vec: Vec<GtElement> = Vec::with_capacity(pp.n_prime);
    for k in 0..pp.n_prime {
        let mut first_basis_prod = get_bls_gt_zero();
        for j in 0..pp.m {
            first_basis_prod += (pp.g_hat_mat[j][0] * (-G_p_T[j][k]));
        }
        first_basis_vec.push(first_basis_prod);
        second_basis_vec.push(pp.g_hat_mat[0][0] * (-h_p_T[0][k]));
        third_basis_vec.push(pp.g_hat_mat[k][0]);
    }
    let mu_T_flat: Vec<ZpElement> = mu_T[0]
        .iter()
        .map(|m| ZpElement::from(*m as u64))
        .collect();
    let equal_proof = zkmmeq::prove(
        &pp.zk_matrix_srs,
        &vec![cached.e1_blind, cached.e3_blind],
        &vec![cached.e1_r, e_3_r],
        &vec![&first_basis_vec, &second_basis_vec],
        &mu_T_flat,
        &third_basis_vec,
        cached.mu_blind,
        cached.mu_r,
    );

    // Delta ZKRP (-b_s^T·lambda_s - h_p^T·mu_s - 1)
    // println!("proving -b.lambda - h.mu - 1 ZKRP (cached)");
    let b_lambda_h_mu_one: Vec<Vec<i64>> =
        vec![vec![cached.e2_scalar + cached.e3_scalar - 1]];
    let mat_b_lambda_h_mu_one =
        vec_mat_to_zkmatrix_i64("-b.lambda - h.mu - 1".to_string(), &b_lambda_h_mu_one);
    let b_lambda_h_mu_one_l =
        mat_b_lambda_h_mu_one.shape.0 * mat_b_lambda_h_mu_one.shape.1;
    let mut b_lambda_h_mu_one_g_i = pp.g_i_vec[0..2 * b_lambda_h_mu_one_l].to_vec();
    b_lambda_h_mu_one_g_i[b_lambda_h_mu_one_l] = get_bls_g1_zero();
    let b_lambda_h_mu_one_zkrp_pp = zkrp::ZKRPParams {
        l: b_lambda_h_mu_one_l,
        m: mat_b_lambda_h_mu_one.shape.0,
        n: mat_b_lambda_h_mu_one.shape.1,
        g_blstrs: pp.g_blstrs,
        g_bls: pp.g_bls,
        g_prime: pp.g_prime,
        g_prime_alpha: pp.g_prime_alpha,
        g_i: pp.g_i_vec.clone(),
        h: pp.h,
        h_prime: pp.h_prime,
        zk_matrix_srs: pp.zk_matrix_srs.clone(),
        pc_gens: pp.pc_gens.clone(),
        bp_gens: pp.bp_gens.clone(),
    };
    let b_lambda_h_mu_one_zkrp_proof = zkrp::prove(
        &b_lambda_h_mu_one_zkrp_pp,
        &b_lambda_h_mu_one,
        c_u,
        c_u_r,
        pp.big_M,
        alpha,
    );

    ZkpProofCached {
        c_A: cached.A_blind,
        c_b: cached.b_blind,
        c_lambda: cached.lambda_blind,
        c_mu: cached.mu_blind,
        c_e_1: cached.e1_blind,
        c_e_2: cached.e2_blind,
        c_e_3: cached.e3_blind,
        A_lambda_e1_proof: cached.e1_zkmm.clone(),
        b_lambda_e2_proof: cached.e2_zkmm.clone(),
        equal_proof,
        lambda_zkrp_proof: cached.lambda_zkrp.clone(),
        mu_zkrp_proof: cached.mu_zkrp.clone(),
        A_plus_M_zkrp_proof: cached.A_plus_M_zkrp.clone(),
        neg_b_plus_M_zkrp_proof: cached.neg_b_plus_M_zkrp.clone(),
        b_lambda_h_mu_one_zkrp_proof,
    }
}

pub fn verify_A_plus_M_zkrp(
    pp: &ZkpSRSCached,
    c_A: GtElement,
    M_m_n_comm: GtElement,
    proof: &ZKRPProof,
    big_M: u32,
) -> bool {
    let A_prime_comm = c_A + M_m_n_comm;
    let A_plus_M_l = pp.m * pp.n;
    let mut A_plus_M_g_i = pp.g_i_vec[0..2 * A_plus_M_l].to_vec();
    A_plus_M_g_i[A_plus_M_l] = get_bls_g1_zero();
    let A_plus_M_zkrp_pp = zkrp::ZKRPParams {
        l: A_plus_M_l, m: pp.m, n: pp.n,
        g_blstrs: pp.g_blstrs, g_bls: pp.g_bls,
        g_prime: pp.g_prime, g_prime_alpha: pp.g_prime_alpha,
        g_i: pp.g_i_vec.clone(),
        h: pp.h, h_prime: pp.h_prime,
        zk_matrix_srs: pp.zk_matrix_srs.clone(),
        pc_gens: pp.pc_gens.clone(), bp_gens: pp.bp_gens.clone(),
    };
    proof.verify(&A_plus_M_zkrp_pp, A_prime_comm, 2 * big_M)
}

pub fn verify_neg_b_plus_M_zkrp(
    pp: &ZkpSRSCached,
    c_b: GtElement,
    M_1_n_comm: GtElement,
    proof: &ZKRPProof,
    big_M: u32,
) -> bool {
    let b_prime_comm = c_b + M_1_n_comm;
    let neg_b_plus_M_l = pp.n;
    let mut neg_b_plus_M_g_i = pp.g_i_vec[0..2 * neg_b_plus_M_l].to_vec();
    neg_b_plus_M_g_i[neg_b_plus_M_l] = get_bls_g1_zero();
    let neg_b_plus_M_zkrp_pp = zkrp::ZKRPParams {
        l: neg_b_plus_M_l, m: 1usize, n: pp.n,
        g_blstrs: pp.g_blstrs, g_bls: pp.g_bls,
        g_prime: pp.g_prime, g_prime_alpha: pp.g_prime_alpha,
        g_i: pp.g_i_vec.clone(),
        h: pp.h, h_prime: pp.h_prime,
        zk_matrix_srs: pp.zk_matrix_srs.clone(),
        pc_gens: pp.pc_gens.clone(), bp_gens: pp.bp_gens.clone(),
    };
    proof.verify(&neg_b_plus_M_zkrp_pp, b_prime_comm, 2 * big_M)
}

pub fn verify_lambda_zkrp(
    pp: &ZkpSRSCached,
    c_lambda: GtElement,
    proof: &ZKRPProof,
    big_M: u32,
) -> bool {
    let lambda_l = pp.n;
    let mut lambda_g_i = pp.g_i_vec[0..2 * lambda_l].to_vec();
    lambda_g_i[lambda_l] = get_bls_g1_zero();
    let lambda_zkrp_pp = zkrp::ZKRPParams {
        l: lambda_l, m: pp.n, n: 1usize,
        g_blstrs: pp.g_blstrs, g_bls: pp.g_bls,
        g_prime: pp.g_prime, g_prime_alpha: pp.g_prime_alpha,
        g_i: pp.g_i_vec.clone(),
        h: pp.h, h_prime: pp.h_prime,
        zk_matrix_srs: pp.zk_matrix_srs.clone(),
        pc_gens: pp.pc_gens.clone(), bp_gens: pp.bp_gens.clone(),
    };
    proof.verify(&lambda_zkrp_pp, c_lambda, big_M)
}

pub fn verify_mu_zkrp(
    pp: &ZkpSRSCached,
    c_mu: GtElement,
    proof: &ZKRPProof,
    big_M: u32,
) -> bool {
    let mu_l = pp.n_prime;
    let mut mu_g_i = pp.g_i_vec[0..2 * mu_l].to_vec();
    mu_g_i[mu_l] = get_bls_g1_zero();
    let mu_zkrp_pp = zkrp::ZKRPParams {
        l: mu_l, m: pp.n_prime, n: 1usize,
        g_blstrs: pp.g_blstrs, g_bls: pp.g_bls,
        g_prime: pp.g_prime, g_prime_alpha: pp.g_prime_alpha,
        g_i: pp.g_i_vec.clone(),
        h: pp.h, h_prime: pp.h_prime,
        zk_matrix_srs: pp.zk_matrix_srs.clone(),
        pc_gens: pp.pc_gens.clone(), bp_gens: pp.bp_gens.clone(),
    };
    proof.verify(&mu_zkrp_pp, c_mu, big_M)
}

pub fn verify_e1_zkmm(
    pp: &ZkpSRSCached,
    c_A: GtElement,
    c_lambda: GtElement,
    c_e1: GtElement,
    proof: &TranSeq,
) -> bool {
    let verifier = ZkMatMul::new(
        c_e1, c_A, c_lambda,
        pp.A_lambda_e1_dims.m, pp.A_lambda_e1_dims.n, pp.A_lambda_e1_dims.l,
    );
    verifier.verify(&pp.zk_matrix_srs, &mut proof.clone())
}

pub fn verify_e2_zkmm(
    pp: &ZkpSRSCached,
    c_b: GtElement,
    c_lambda: GtElement,
    c_e2: GtElement,
    proof: &TranSeq,
) -> bool {
    let verifier = ZkMatMul::new(
        c_e2, c_b, c_lambda,
        pp.b_lambda_e2_dims.m, pp.b_lambda_e2_dims.n, pp.b_lambda_e2_dims.l,
    );
    verifier.verify(&pp.zk_matrix_srs, &mut proof.clone())
}

impl ZkpProofCached {
    pub fn verify(
        &self,
        pp: &ZkpSRSCached,
        G_p_T: &Vec<Vec<i64>>,
        h_p_T: &Vec<Vec<i64>>,
        flags: &VerifierCacheFlagsCached,
        fixed_comms: Option<&PrecomputedFixedComms>,
    ) -> bool {
        let (M_m_n_comm, M_1_n_comm, neg_one_comm) = if let Some(fc) = fixed_comms {
            (fc.M_m_n_comm, fc.M_1_n_comm, fc.neg_one_comm)
        } else {
            let mut M_m_n: Vec<Vec<i64>> = vec![];
            for _ in 0..pp.m {
                M_m_n.push(vec![pp.big_M as i64; pp.n]);
            }
            let mat_M_m_n = vec_mat_to_zkmatrix_i64("M_m_n".to_string(), &M_m_n);
            let (M_m_n_comm_fresh, _) = mat_M_m_n.commit_rm(&pp.zk_matrix_srs);

            let mut M_1_n: Vec<Vec<i64>> = vec![vec![pp.big_M as i64; pp.n]];
            let mat_M_1_n = vec_mat_to_zkmatrix_i64("M_1_n".to_string(), &M_1_n);
            let (M_1_n_comm_fresh, _) = mat_M_1_n.commit_rm(&pp.zk_matrix_srs);

            let neg_one = vec![vec![-1i64]];
            let mat_neg_one = vec_mat_to_zkmatrix_i128("-1".to_string(), &neg_one);
            let (neg_one_comm_fresh, _) = mat_neg_one.commit_cm(&pp.zk_matrix_srs);

            (M_m_n_comm_fresh, M_1_n_comm_fresh, neg_one_comm_fresh)
        };
        let A_prime_comm = self.c_A + M_m_n_comm;
        let b_prime_comm = self.c_b + M_1_n_comm;
        let c_u = self.c_e_2 + self.c_e_3 + neg_one_comm;

        //Verify A_s^T . lambda_s = e_1
        if !flags.is_e1_cached {
            // println!("verifying A . lambda = e1");
            let A_lambda_e1_verifier = ZkMatMul::new(
                self.c_e_1,
                self.c_A,
                self.c_lambda,
                pp.A_lambda_e1_dims.m,
                pp.A_lambda_e1_dims.n,
                pp.A_lambda_e1_dims.l,
            );
            let A_lambda_e1_verified =
                A_lambda_e1_verifier.verify(&pp.zk_matrix_srs, &mut self.A_lambda_e1_proof.clone());
            if !A_lambda_e1_verified {
                println!("Failed to verify A_s^T . lambda_s = e_1");
                return false;
            }
        }

        //Verify -b_s^T . lambda_s = e_2
        if !flags.is_e2_cached {
            // println!("verifying -b . lambda = e2");
            let b_lambda_e2_verifier = ZkMatMul::new(
                self.c_e_2,
                self.c_b,
                self.c_lambda,
                pp.b_lambda_e2_dims.m,
                pp.b_lambda_e2_dims.n,
                pp.b_lambda_e2_dims.l,
            );
            let b_lambda_e2_verified =
                b_lambda_e2_verifier.verify(&pp.zk_matrix_srs, &mut self.b_lambda_e2_proof.clone());
            if !b_lambda_e2_verified {
                println!("Failed to verify -b_s^T . lambda_s = e_2");
                return false;
            }
        }

        //Verify EqualProof
        // println!("verifying equal");
        let mut first_basis_vec: Vec<GtElement> = Vec::with_capacity(pp.n_prime);
        let mut second_basis_vec: Vec<GtElement> = Vec::with_capacity(pp.n_prime);
        let mut third_basis_vec: Vec<GtElement> = Vec::with_capacity(pp.n_prime);
        for k in 0..pp.n_prime {
            let mut first_basis_prod = get_bls_gt_zero();
            for j in 0..pp.m {
                first_basis_prod += (pp.g_hat_mat[j][0] * (-G_p_T[j][k]));
            }
            first_basis_vec.push(first_basis_prod);
            second_basis_vec.push(pp.g_hat_mat[0][0] * (-h_p_T[0][k]));
            third_basis_vec.push(pp.g_hat_mat[k][0])
        }
        let equal_verified = self.equal_proof.verify(
            &pp.zk_matrix_srs,
            &vec![self.c_e_1, self.c_e_3],
            &vec![&first_basis_vec, &second_basis_vec],
            &third_basis_vec,
            self.c_mu,
        );
        if !equal_verified {
            println!("Error verified equals proof");
            return false;
        }

        //Verify ZKRP proofs
        //First, lambda
        let lambda_zkrp_verified: bool;
        if flags.is_lambda_cached {
            lambda_zkrp_verified = true;
        } else {
            // println!("verifying lambda zkrp");
            let mut lambda_g_hat_i: Vec<GtElement> = Vec::with_capacity(pp.n);
            for j in 0..pp.n {
                for k in 0..1 {
                    lambda_g_hat_i.push(pp.g_hat_mat[j][k].clone());
                }
            }
            let lambda_l = pp.n;
            let mut lambda_g_i = pp.g_i_vec[0..2 * lambda_l].to_vec();
            lambda_g_i[lambda_l] = get_bls_g1_zero();
            let lambda_zkrp_pp = zkrp::ZKRPParams {
                l: lambda_l,
                m: pp.n,
                n: 1usize,
                g_blstrs: pp.g_blstrs,
                g_bls: pp.g_bls,
                g_prime: pp.g_prime,
                g_prime_alpha: pp.g_prime_alpha,
                g_i: pp.g_i_vec.clone(),
                h: pp.h,
                h_prime: pp.h_prime,
                zk_matrix_srs: pp.zk_matrix_srs.clone(),
                pc_gens: pp.pc_gens.clone(),
                bp_gens: pp.bp_gens.clone(),
            };
            lambda_zkrp_verified =
                self.lambda_zkrp_proof
                    .verify(&lambda_zkrp_pp, self.c_lambda, pp.big_M);
        }
        if !lambda_zkrp_verified {
            println!("Failed to verify ZKRP of lambda");
            return false;
        }

        //Next, mu
        let mu_zkrp_verified: bool;
        if flags.is_mu_cached {
            mu_zkrp_verified = true;
        } else {
            // println!("verifying mu zkrp");
            let mut mu_g_hat_i: Vec<GtElement> = Vec::with_capacity(pp.n_prime);
            for j in 0..pp.n_prime {
                for k in 0..1 {
                    mu_g_hat_i.push(pp.g_hat_mat[j][k].clone());
                }
            }
            let mu_l = pp.n_prime;
            let mut mu_g_i = pp.g_i_vec[0..2 * mu_l].to_vec();
            mu_g_i[mu_l] = get_bls_g1_zero();
            let mu_zkrp_pp = zkrp::ZKRPParams {
                l: mu_l,
                m: pp.n_prime,
                n: 1usize,
                g_blstrs: pp.g_blstrs,
                g_bls: pp.g_bls,
                g_prime: pp.g_prime,
                g_prime_alpha: pp.g_prime_alpha,
                g_i: pp.g_i_vec.clone(),
                h: pp.h,
                h_prime: pp.h_prime,
                zk_matrix_srs: pp.zk_matrix_srs.clone(),
                pc_gens: pp.pc_gens.clone(),
                bp_gens: pp.bp_gens.clone(),
            };
            mu_zkrp_verified = self.mu_zkrp_proof.verify(&mu_zkrp_pp, self.c_mu, pp.big_M);
        }
        if !mu_zkrp_verified {
            println!("Failed to verify ZKRP of mu");
            return false;
        }

        //Next, c_prime_A
        // println!("verifying A+M zkrp");
        let A_plus_M_zkrp_verified: bool;
        if flags.is_A_plus_M_cached {
            A_plus_M_zkrp_verified = true;
        } else {
            let mut A_plus_M_g_hat_i: Vec<GtElement> = Vec::with_capacity(pp.n * pp.m);
            for j in 0..pp.m {
                for k in 0..pp.n {
                    A_plus_M_g_hat_i.push(pp.g_hat_mat[j][k].clone());
                }
            }
            let A_plus_M_l = pp.m * pp.n;
            let mut A_plus_M_g_i = pp.g_i_vec[0..2 * A_plus_M_l].to_vec();
            A_plus_M_g_i[A_plus_M_l] = get_bls_g1_zero();
            let A_plus_M_zkrp_pp = zkrp::ZKRPParams {
                l: A_plus_M_l,
                m: pp.m,
                n: pp.n,
                g_blstrs: pp.g_blstrs,
                g_bls: pp.g_bls,
                g_prime: pp.g_prime,
                g_prime_alpha: pp.g_prime_alpha,
                g_i: pp.g_i_vec.clone(),
                h: pp.h,
                h_prime: pp.h_prime,
                zk_matrix_srs: pp.zk_matrix_srs.clone(),
                pc_gens: pp.pc_gens.clone(),
                bp_gens: pp.bp_gens.clone(),
            };
            A_plus_M_zkrp_verified =
                self.A_plus_M_zkrp_proof
                    .verify(&A_plus_M_zkrp_pp, A_prime_comm, 2 * pp.big_M);
        }
        if !A_plus_M_zkrp_verified {
            println!("Failed to verify ZKRP of A+M");
            return false;
        }

        //Next, c_prime_b
        // println!("verifying -b+M zkrp");
        let neg_b_plus_M_zkrp_verified: bool;
        if flags.is_neg_b_plus_M_cached {
            neg_b_plus_M_zkrp_verified = true;
        } else {
            let mut neg_b_plus_M_g_hat_i: Vec<GtElement> = Vec::with_capacity(pp.n);
            for j in 0..1usize {
                for k in 0..pp.n {
                    neg_b_plus_M_g_hat_i.push(pp.g_hat_mat[j][k].clone());
                }
            }
            let neg_b_plus_M_l = pp.n;
            let mut neg_b_plus_M_g_i = pp.g_i_vec[0..2 * neg_b_plus_M_l].to_vec();
            neg_b_plus_M_g_i[neg_b_plus_M_l] = get_bls_g1_zero();
            let neg_b_plus_M_zkrp_pp = zkrp::ZKRPParams {
                l: neg_b_plus_M_l,
                m: 1usize,
                n: pp.n,
                g_blstrs: pp.g_blstrs,
                g_bls: pp.g_bls,
                g_prime: pp.g_prime,
                g_prime_alpha: pp.g_prime_alpha,
                g_i: pp.g_i_vec.clone(),
                h: pp.h,
                h_prime: pp.h_prime,
                zk_matrix_srs: pp.zk_matrix_srs.clone(),
                pc_gens: pp.pc_gens.clone(),
                bp_gens: pp.bp_gens.clone(),
            };
            neg_b_plus_M_zkrp_verified = self.neg_b_plus_M_zkrp_proof.verify(
                &neg_b_plus_M_zkrp_pp,
                b_prime_comm,
                2 * pp.big_M,
            );
        }
        if !neg_b_plus_M_zkrp_verified {
            println!("Failed to verify ZKRP of -b+M");
            return false;
        }

        //Finally, -b_s^T.lambda_s - h_p^T.mu_s - 1
        // println!("verifying -b_s^T.lambda_s - h_p^T.mu_s - 1 zkrp");
        let b_lambda_h_mu_one_zkrp_pp = zkrp::ZKRPParams {
            l: 1usize,
            m: 1usize,
            n: 1usize,
            g_blstrs: pp.g_blstrs,
            g_bls: pp.g_bls,
            g_prime: pp.g_prime,
            g_prime_alpha: pp.g_prime_alpha,
            g_i: pp.g_i_vec.clone(),
            h: pp.h,
            h_prime: pp.h_prime,
            zk_matrix_srs: pp.zk_matrix_srs.clone(),
            pc_gens: pp.pc_gens.clone(),
            bp_gens: pp.bp_gens.clone(),
        };
        let b_lambda_h_mu_one_zkrp_verified =
            self.b_lambda_h_mu_one_zkrp_proof
                .verify(&b_lambda_h_mu_one_zkrp_pp, c_u, pp.big_M);
        if !b_lambda_h_mu_one_zkrp_verified {
            println!("Failed to verify ZKRP of -b.lambda - h.mu - 1");
            return false;
        }

        return true;
    }
}
