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

#[derive(Clone, Default)]
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

#[derive(Clone, Default)]
pub struct ZkpDims {
    pub m: usize,
    pub n: usize,
    pub n_prime: usize,
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

pub fn prove_cached(
    pp: &ZkpSRSCached,
    dims: &ZkpDims,
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
    let mut first_basis_vec: Vec<GtElement> = Vec::with_capacity(dims.n_prime);
    let mut second_basis_vec: Vec<GtElement> = Vec::with_capacity(dims.n_prime);
    let mut third_basis_vec: Vec<GtElement> = Vec::with_capacity(dims.n_prime);
    for k in 0..dims.n_prime {
        let mut first_basis_prod = get_bls_gt_zero();
        for j in 0..dims.m {
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
    dims: &ZkpDims,
    c_A: GtElement,
    M_m_n_comm: GtElement,
    proof: &ZKRPProof,
    big_M: u32,
) -> bool {
    let A_prime_comm = c_A + M_m_n_comm;
    let A_plus_M_l = dims.m * dims.n;
    let mut A_plus_M_g_i = pp.g_i_vec[0..2 * A_plus_M_l].to_vec();
    A_plus_M_g_i[A_plus_M_l] = get_bls_g1_zero();
    let A_plus_M_zkrp_pp = zkrp::ZKRPParams {
        l: A_plus_M_l, m: dims.m, n: dims.n,
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
    dims: &ZkpDims,
    c_b: GtElement,
    M_1_n_comm: GtElement,
    proof: &ZKRPProof,
    big_M: u32,
) -> bool {
    let b_prime_comm = c_b + M_1_n_comm;
    let neg_b_plus_M_l = dims.n;
    let mut neg_b_plus_M_g_i = pp.g_i_vec[0..2 * neg_b_plus_M_l].to_vec();
    neg_b_plus_M_g_i[neg_b_plus_M_l] = get_bls_g1_zero();
    let neg_b_plus_M_zkrp_pp = zkrp::ZKRPParams {
        l: neg_b_plus_M_l, m: 1usize, n: dims.n,
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
    dims: &ZkpDims,
    c_lambda: GtElement,
    proof: &ZKRPProof,
    big_M: u32,
) -> bool {
    let lambda_l = dims.n;
    let mut lambda_g_i = pp.g_i_vec[0..2 * lambda_l].to_vec();
    lambda_g_i[lambda_l] = get_bls_g1_zero();
    let lambda_zkrp_pp = zkrp::ZKRPParams {
        l: lambda_l, m: dims.n, n: 1usize,
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
    dims: &ZkpDims,
    c_mu: GtElement,
    proof: &ZKRPProof,
    big_M: u32,
) -> bool {
    let mu_l = dims.n_prime;
    let mut mu_g_i = pp.g_i_vec[0..2 * mu_l].to_vec();
    mu_g_i[mu_l] = get_bls_g1_zero();
    let mu_zkrp_pp = zkrp::ZKRPParams {
        l: mu_l, m: dims.n_prime, n: 1usize,
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
    dims: &ZkpDims,
    c_A: GtElement,
    c_lambda: GtElement,
    c_e1: GtElement,
    proof: &TranSeq,
) -> bool {
    let verifier = ZkMatMul::new(
        c_e1, c_A, c_lambda,
        dims.A_lambda_e1_dims.m, dims.A_lambda_e1_dims.n, dims.A_lambda_e1_dims.l,
    );
    verifier.verify(&pp.zk_matrix_srs, &mut proof.clone())
}

pub fn verify_e2_zkmm(
    pp: &ZkpSRSCached,
    dims: &ZkpDims,
    c_b: GtElement,
    c_lambda: GtElement,
    c_e2: GtElement,
    proof: &TranSeq,
) -> bool {
    let verifier = ZkMatMul::new(
        c_e2, c_b, c_lambda,
        dims.b_lambda_e2_dims.m, dims.b_lambda_e2_dims.n, dims.b_lambda_e2_dims.l,
    );
    verifier.verify(&pp.zk_matrix_srs, &mut proof.clone())
}

impl ZkpProofCached {
    pub fn verify(
        &self,
        pp: &ZkpSRSCached,
        dims: &ZkpDims,
        G_p_T: &Vec<Vec<i64>>,
        h_p_T: &Vec<Vec<i64>>,
        flags: &VerifierCacheFlagsCached,
        fixed_comms: Option<&PrecomputedFixedComms>,
    ) -> bool {
        let (M_m_n_comm, M_1_n_comm, neg_one_comm) = if let Some(fc) = fixed_comms {
            (fc.M_m_n_comm, fc.M_1_n_comm, fc.neg_one_comm)
        } else {
            let mut M_m_n: Vec<Vec<i64>> = vec![];
            for _ in 0..dims.m {
                M_m_n.push(vec![pp.big_M as i64; dims.n]);
            }
            let mat_M_m_n = vec_mat_to_zkmatrix_i64("M_m_n".to_string(), &M_m_n);
            let (M_m_n_comm_fresh, _) = mat_M_m_n.commit_rm(&pp.zk_matrix_srs);

            let mut M_1_n: Vec<Vec<i64>> = vec![vec![pp.big_M as i64; dims.n]];
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
                dims.A_lambda_e1_dims.m,
                dims.A_lambda_e1_dims.n,
                dims.A_lambda_e1_dims.l,
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
                dims.b_lambda_e2_dims.m,
                dims.b_lambda_e2_dims.n,
                dims.b_lambda_e2_dims.l,
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
        let mut first_basis_vec: Vec<GtElement> = Vec::with_capacity(dims.n_prime);
        let mut second_basis_vec: Vec<GtElement> = Vec::with_capacity(dims.n_prime);
        let mut third_basis_vec: Vec<GtElement> = Vec::with_capacity(dims.n_prime);
        for k in 0..dims.n_prime {
            let mut first_basis_prod = get_bls_gt_zero();
            for j in 0..dims.m {
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
            let mut lambda_g_hat_i: Vec<GtElement> = Vec::with_capacity(dims.n);
            for j in 0..dims.n {
                for k in 0..1 {
                    lambda_g_hat_i.push(pp.g_hat_mat[j][k].clone());
                }
            }
            let lambda_l = dims.n;
            let mut lambda_g_i = pp.g_i_vec[0..2 * lambda_l].to_vec();
            lambda_g_i[lambda_l] = get_bls_g1_zero();
            let lambda_zkrp_pp = zkrp::ZKRPParams {
                l: lambda_l,
                m: dims.n,
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
            let mut mu_g_hat_i: Vec<GtElement> = Vec::with_capacity(dims.n_prime);
            for j in 0..dims.n_prime {
                for k in 0..1 {
                    mu_g_hat_i.push(pp.g_hat_mat[j][k].clone());
                }
            }
            let mu_l = dims.n_prime;
            let mut mu_g_i = pp.g_i_vec[0..2 * mu_l].to_vec();
            mu_g_i[mu_l] = get_bls_g1_zero();
            let mu_zkrp_pp = zkrp::ZKRPParams {
                l: mu_l,
                m: dims.n_prime,
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
            let mut A_plus_M_g_hat_i: Vec<GtElement> = Vec::with_capacity(dims.n * dims.m);
            for j in 0..dims.m {
                for k in 0..dims.n {
                    A_plus_M_g_hat_i.push(pp.g_hat_mat[j][k].clone());
                }
            }
            let A_plus_M_l = dims.m * dims.n;
            let mut A_plus_M_g_i = pp.g_i_vec[0..2 * A_plus_M_l].to_vec();
            A_plus_M_g_i[A_plus_M_l] = get_bls_g1_zero();
            let A_plus_M_zkrp_pp = zkrp::ZKRPParams {
                l: A_plus_M_l,
                m: dims.m,
                n: dims.n,
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
            let mut neg_b_plus_M_g_hat_i: Vec<GtElement> = Vec::with_capacity(dims.n);
            for j in 0..1usize {
                for k in 0..dims.n {
                    neg_b_plus_M_g_hat_i.push(pp.g_hat_mat[j][k].clone());
                }
            }
            let neg_b_plus_M_l = dims.n;
            let mut neg_b_plus_M_g_i = pp.g_i_vec[0..2 * neg_b_plus_M_l].to_vec();
            neg_b_plus_M_g_i[neg_b_plus_M_l] = get_bls_g1_zero();
            let neg_b_plus_M_zkrp_pp = zkrp::ZKRPParams {
                l: neg_b_plus_M_l,
                m: 1usize,
                n: dims.n,
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
