use bls_bulletproofs::{BulletproofGens, PedersenGens};
use blstrs;
use zkmatrix::commit_mat::CommitMat;
use zkmatrix::setup::SRS;
use zkmatrix::utils::curve::{G1Element, G2Element, GtElement, ZpElement};

use crate::utils::*;
use crate::zkmmeq;
use crate::zkmmeq::EqualProof;
use crate::zkrp;
use crate::zkrp::ZKRPProof;

pub struct ExecutionProofParams {
    pub m: usize,
    pub n: usize,
    pub srs: SRS,
    pub g_hat_mat: Vec<Vec<GtElement>>,
    pub g_i_vec: Vec<G1Element>,
    pub g_blstrs: blstrs::G1Affine,
    pub g_bls: G1Element,
    pub g_prime: G2Element,
    pub g_prime_alpha: G2Element,
    pub h: G1Element,
    pub h_prime: G2Element,
    pub pc_gens: PedersenGens,
    pub bp_gens: BulletproofGens,
    pub big_M: u32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ExecutionProof {
    pub c_Ay: GtElement,
    pub equal_proof: EqualProof,
    pub w_zkrp_proof: ZKRPProof,
}

pub fn prove(
    pp: &ExecutionProofParams,
    A_s: &Vec<Vec<i64>>,
    b_s: &Vec<Vec<i64>>,
    y: &Vec<Vec<i64>>,
    c_A: GtElement,
    r_A: ZpElement,
    c_b: GtElement,
    r_b: ZpElement,
    alpha: ZpElement,
) -> ExecutionProof {
    let m = pp.m;
    let n = pp.n;

    assert!(A_s.len() == m && A_s[0].len() == n);
    assert!(b_s.len() == m && b_s[0].len() == 1);
    assert!(y.len() == n && y[0].len() == 1);

    // Step 1: Compute A_y = A_s * y (matrix-vector product, m x 1)
    let mut A_y: Vec<Vec<i64>> = vec![vec![0i64; 1]; m];
    for i in 0..m {
        let mut sum = 0i64;
        for j in 0..n {
            sum += A_s[i][j] * y[j][0];
        }
        A_y[i][0] = sum;
    }

    // Step 2: Sample fresh randomness for A_y
    let r_Ay = ZpElement::rand();

    // Step 3: Commit to A_y
    let mat_A_y = vec_mat_to_zkmatrix_i64("A_y".to_string(), &A_y);
    let (c_Ay_comm, _) = mat_A_y.commit_cm(&pp.srs);
    let c_Ay = c_Ay_comm + (r_Ay * pp.srs.blind_base);

    // Step 4: Compute residual w = b_s - A_y (m x 1)
    let mut w: Vec<Vec<i64>> = vec![vec![0i64; 1]; m];
    for i in 0..m {
        w[i][0] = b_s[i][0] - A_y[i][0];
    }
    // Step 5: Compute residual randomness and commitment
    let r_w = r_b - r_Ay;
    let c_w = c_b - c_Ay;

    // DEBUG: Check this commits as expected
    // let mat_w = vec_mat_to_zkmatrix_i64("w_test".to_string(), &w);
    // let (c_w_comm, _) = mat_w.commit_rm(&pp.srs);
    // let c_w_verif = c_w_comm + (r_w * pp.srs.blind_base);
    // assert!(c_w_verif == c_w);

    // Step 6: Build zkmmeq proof that A_y = A_s * y
    // g_j[0] encodes the linear combination with y:
    //   g_j[0][i*n + j] = g_hat_mat[i][0] * y[j]
    let l = m * n;
    let mut y_basis: Vec<GtElement> = Vec::with_capacity(l);
    for i in 0..m {
        for j in 0..n {
            let y_scalar = i64_to_zp(y[j][0]);
            y_basis.push(pp.g_hat_mat[i][0] * y_scalar);
        }
    }

    // x = A_s_flat, the flattened A_s matrix (use signed conversion)
    let mut A_s_flat: Vec<ZpElement> = Vec::with_capacity(l);
    for i in 0..m {
        for j in 0..n {
            A_s_flat.push(i64_to_zp(A_s[i][j]));
        }
    }

    // g_x is the standard commit_rm basis for A_s
    let mut g_x: Vec<GtElement> = Vec::with_capacity(l);
    for i in 0..m {
        for j in 0..n {
            g_x.push(pp.g_hat_mat[i][j].clone());
        }
    }

    let equal_proof = zkmmeq::prove(
        &pp.srs,
        &vec![c_Ay],
        &vec![r_Ay],
        &vec![&y_basis],
        &A_s_flat,
        &g_x,
        c_A,
        r_A,
    );

    // Step 7: Build ZKRP proof for w in [0, big_M]
    let w_l = m;
    let mut w_g_i = pp.g_i_vec[0..2 * w_l].to_vec();
    w_g_i[w_l] = get_bls_g1_zero();
    let w_zkrp_pp = zkrp::ZKRPParams {
        l: w_l,
        m: m,
        n: 1,
        g_blstrs: pp.g_blstrs,
        g_bls: pp.g_bls,
        g_prime: pp.g_prime,
        g_prime_alpha: pp.g_prime_alpha,
        g_i: pp.g_i_vec.clone(),
        h: pp.h,
        h_prime: pp.h_prime,
        zk_matrix_srs: pp.srs.clone(),
        pc_gens: pp.pc_gens.clone(),
        bp_gens: pp.bp_gens.clone(),
    };
    let w_zkrp_proof = zkrp::prove(&w_zkrp_pp, &w, c_w, r_w, pp.big_M, alpha);

    ExecutionProof {
        c_Ay,
        equal_proof,
        w_zkrp_proof,
    }
}

impl ExecutionProof {
    pub fn verify(
        &self,
        pp: &ExecutionProofParams,
        y: &Vec<Vec<i64>>,
        c_A: GtElement,
        c_b: GtElement,
    ) -> bool {
        let m = pp.m;
        let n = pp.n;

        // Step 1: Compute c_w = c_b - c_Ay
        let c_w = c_b - self.c_Ay;

        // Step 2: Verify zkmmeq proof
        let l = m * n;
        let mut y_basis: Vec<GtElement> = Vec::with_capacity(l);
        for i in 0..m {
            for j in 0..n {
                let y_scalar = i64_to_zp(y[j][0]);
                y_basis.push(pp.g_hat_mat[i][0] * y_scalar);
            }
        }
        let mut g_x: Vec<GtElement> = Vec::with_capacity(l);
        for i in 0..m {
            for j in 0..n {
                g_x.push(pp.g_hat_mat[i][j].clone());
            }
        }
        let equal_verified = self.equal_proof.verify(
            &pp.srs,
            &vec![self.c_Ay],
            &vec![&y_basis],
            &g_x,
            c_A,
        );
        if !equal_verified {
            println!("Failed to verify zkmmeq proof for A_s * y = A_y");
            return false;
        }

        // Step 3: Verify ZKRP proof for w in [0, big_M]
        let w_l = m;
        let mut w_g_i = pp.g_i_vec[0..2 * w_l].to_vec();
        w_g_i[w_l] = get_bls_g1_zero();
        let w_zkrp_pp = zkrp::ZKRPParams {
            l: w_l,
            m: m,
            n: 1,
            g_blstrs: pp.g_blstrs,
            g_bls: pp.g_bls,
            g_prime: pp.g_prime,
            g_prime_alpha: pp.g_prime_alpha,
            g_i: pp.g_i_vec.clone(),
            h: pp.h,
            h_prime: pp.h_prime,
            zk_matrix_srs: pp.srs.clone(),
            pc_gens: pp.pc_gens.clone(),
            bp_gens: pp.bp_gens.clone(),
        };
        let w_zkrp_verified = self.w_zkrp_proof.verify(&w_zkrp_pp, c_w, pp.big_M);
        if !w_zkrp_verified {
            println!("Failed to verify ZKRP of residual w");
            return false;
        }

        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use group::ff::Field;
    use rand::SeedableRng;
    use rand_chacha::ChaChaRng;
    use rayon::prelude::*;
    use zkmatrix::setup::SRS as ZkSrs;

    fn setup_test_env(
        m: usize,
        n: usize,
    ) -> (ExecutionProofParams, ZpElement) {
        let mut pc_gens = PedersenGens::default();
        let bp_gens = BulletproofGens::new(64, 2 * m * n);

        let g_blstrs: blstrs::G1Affine = pc_gens.B.into();
        let g_bls = blstrs_affine_to_bls_g1(&g_blstrs);

        let (throwaway_srs, alpha) =
            ZkSrs::new_with_chosen_g_return_s_hat(32, g_bls);
        let g_prime = throwaway_srs.h_hat.clone();

        let q = std::cmp::max(m, n) + 1;
        let mut alpha_vec: Vec<ZpElement> =
            std::iter::successors(Some(alpha), |&x| Some(x * alpha))
                .take(2 * ((q.pow(2)) - 1))
                .collect();
        alpha_vec.insert(0, ZpElement::from(1u64));
        let g_i_vec: Vec<G1Element> =
            alpha_vec.par_iter().map(|&x| x * g_bls).collect();

        alpha_vec.truncate((q.pow(2) as usize) + 1);
        alpha_vec.remove(0);

        let q_alpha_vec: Vec<ZpElement> =
            std::iter::successors(Some(alpha), |&x| Some(x * alpha))
                .take(q)
                .collect();
        let alpha_pow_q = *q_alpha_vec.last().unwrap();
        let q_i_alpha_vec: Vec<ZpElement> =
            std::iter::successors(Some(alpha_pow_q), |&x| Some(x * alpha_pow_q))
                .take(q)
                .collect();

        let g_hat_j: Vec<G1Element> =
            alpha_vec.par_iter().map(|&x| x * g_bls).collect();
        let g_hat_prime_j: Vec<G1Element> =
            q_i_alpha_vec.par_iter().map(|&x| x * g_bls).collect();
        let g_hat_i: Vec<G2Element> =
            q_i_alpha_vec.par_iter().map(|&x| x * g_prime).collect();
        let g_hat_prime_i: Vec<G2Element> =
            alpha_vec.par_iter().map(|&x| x * g_prime).collect();

        let mut g_hat_mat: Vec<Vec<GtElement>> = vec![];
        for j in 0..q {
            g_hat_mat.push(vec![]);
            for k in 0..q {
                g_hat_mat[j].push(g_hat_j[j] * g_hat_i[k]);
            }
        }

        let mut beta_rng = ChaChaRng::from_seed([42u8; 32]);
        let beta_blstrs = blstrs::Scalar::random(&mut beta_rng);
        let beta = blstrs_to_bls_field_elem(&beta_blstrs);
        let h_blstrs_proj: blstrs::G1Projective = pc_gens.B * beta_blstrs;
        let h = blstrs_proj_to_bls_g1(&h_blstrs_proj);
        let h_prime = g_prime * beta;
        let h_hat = h * g_prime;
        pc_gens.B_blinding = h_blstrs_proj;

        let srs = SRS {
            q: q,
            g_hat: g_bls,
            h_hat: g_prime,
            blind_base: h_hat,
            g_hat_vec: g_hat_j.clone(),
            h_hat_vec: g_hat_i.clone(),
            g_hat_prime_vec: g_hat_prime_j.clone(),
            h_hat_prime_vec: g_hat_prime_i.clone(),
        };

        let big_M = 100;

        let pp = ExecutionProofParams {
            m,
            n,
            srs,
            g_hat_mat,
            g_i_vec,
            g_blstrs,
            g_bls,
            g_prime,
            g_prime_alpha: g_prime * alpha,
            h,
            h_prime,
            pc_gens,
            bp_gens,
            big_M,
        };

        (pp, alpha)
    }

    #[test]
    fn test_execution_proof_basic() {
        let m = 2;
        let n = 2;

        let (pp, alpha) = setup_test_env(m, n);

        // A_s: 2x2 matrix
        let A_s: Vec<Vec<i64>> = vec![vec![1, 2], vec![3, 4]];

        // b_s: 2x1 vector (must be >= A_s * y)
        let b_s: Vec<Vec<i64>> = vec![vec![10], vec![20]];

        // y: 2x1 public vector
        let y: Vec<Vec<i64>> = vec![vec![1], vec![2]];

        // Commit to A_s
        let r_A = ZpElement::rand();
        let mat_A = vec_mat_to_zkmatrix_i64("A_s_test".to_string(), &A_s);
        let (c_A_comm, _) = mat_A.commit_rm(&pp.srs);
        let c_A = c_A_comm + (r_A * pp.srs.blind_base);

        // Commit to b_s
        let r_b = ZpElement::rand();
        let mat_b = vec_mat_to_zkmatrix_i64("b_s_test".to_string(), &b_s);
        let (c_b_comm, _) = mat_b.commit_cm(&pp.srs);
        let c_b = c_b_comm + (r_b * pp.srs.blind_base);

        // Expected A_y = A_s * y = [1*1 + 2*2 = 5, 3*1 + 4*2 = 11]
        // Expected w = b_s - A_y = [10-5=5, 20-11=9] (both >= 0, OK)

        let proof = prove(&pp, &A_s, &b_s, &y, c_A, r_A, c_b, r_b, alpha);
        let verified = proof.verify(&pp, &y, c_A, c_b);
        assert!(verified, "Execution proof should verify");

        // Test with a case where w has negative components (should fail range proof)
        let b_s_small: Vec<Vec<i64>> = vec![vec![3], vec![5]];
        let r_b_small = ZpElement::rand();
        let mat_b_small = vec_mat_to_zkmatrix_i64("b_s_small_test".to_string(), &b_s_small);
        let (c_b_small_comm, _) = mat_b_small.commit_cm(&pp.srs);
        let c_b_small = c_b_small_comm + (r_b_small * pp.srs.blind_base);

        let bad_proof = prove(
            &pp, &A_s, &b_s_small, &y, c_A, r_A, c_b_small, r_b_small, alpha,
        );
        let bad_verified = bad_proof.verify(&pp, &y, c_A, c_b_small);
        assert!(!bad_verified, "Proof with negative residual should NOT verify");
    }

    #[test]
    fn test_execution_proof_minimal_example(){
        let m = 2;
        let n = 2;
        let (pp, alpha) = setup_test_env(m, n);

        /*
            Consider the system be a counter: x' = x + 2
            These A_s, b_s encode that        
        */
        let A_s: Vec<Vec<i64>> = vec![vec![1, -1], vec![-1, 1]];
        let b_s: Vec<Vec<i64>> = vec![vec![-2], vec![2]];
        let y: Vec<Vec<i64>> = vec![vec![4], vec![6]];

        let r_A = ZpElement::rand();
        let mat_A = vec_mat_to_zkmatrix_i64("A_s_test".to_string(), &A_s);
        let (c_A_comm, _) = mat_A.commit_rm(&pp.srs);
        let c_A = c_A_comm + (r_A * pp.srs.blind_base);

        let r_b = ZpElement::rand();
        let mat_b = vec_mat_to_zkmatrix_i64("b_s_test".to_string(), &b_s);
        let (c_b_comm, _) = mat_b.commit_cm(&pp.srs);
        let c_b = c_b_comm + (r_b * pp.srs.blind_base);

        // Expected A_y = A_s * y = [1*4 + -1*6 = -2, -1*4 + 1*6 = 2]
        // Expected w = b_s - A_y = [-2--2=0, 2-2=0] (both >= 0, OK)

        let proof = prove(&pp, &A_s, &b_s, &y, c_A, r_A, c_b, r_b, alpha);
        let verified = proof.verify(&pp, &y, c_A, c_b);
        assert!(verified, "Execution proof should verify");
    }
}
