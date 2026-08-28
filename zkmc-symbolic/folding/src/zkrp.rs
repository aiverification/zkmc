use bls::{G1Element, G2Element, GtElement, ZpElement};
use zkmatrix::utils::curve as bls;
use zkmatrix::utils::fiat_shamir::{TranElem, TranSeq};

use crate::range_proof::ArbitraryUpperRangeProof;
use crate::utils::curve_utils::{
    bls_field_elem_to_blstrs_scalar, blstrs_affine_to_bls_g1, blstrs_to_bls_field_elem,
    get_bls_g1_zero,
};
use bls_bulletproofs::{BulletproofGens, PedersenGens};
use merlin::Transcript;
use rayon::prelude::*;
use zkmatrix::setup::SRS;

pub struct ZKRPParams {
    pub l: usize,
    pub m: usize,
    pub n: usize,
    pub g_blstrs: blstrs::G1Affine,
    pub g_bls: G1Element,
    pub g_prime: G2Element,
    pub g_prime_alpha: G2Element,
    pub g_i: Vec<G1Element>,
    pub h: G1Element,
    pub h_prime: G2Element,
    pub zk_matrix_srs: SRS,
    pub pc_gens: PedersenGens,
    pub bp_gens: BulletproofGens,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ZKRPProof {
    pub c_i: Vec<G1Element>,
    pub pi_eq: G1Element,
    pub theta: G1Element,
    pub v_range_proof: ArbitraryUpperRangeProof,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ZKRPMultiProof {
    pub c_i: Vec<Vec<G1Element>>,
    pub pi_eq: Vec<G1Element>,
    pub theta: Vec<G1Element>,
    pub v_range_proof: ArbitraryUpperRangeProof,
}

pub fn prove_multi(
    pp: &ZKRPParams,
    v: &[Vec<Vec<i64>>],
    c_hat_v: &[GtElement],
    r_A: &[ZpElement],
    b: u32,
    alpha: ZpElement,
    rho_i: &[Vec<blstrs::Scalar>],
) -> ZKRPMultiProof {
    let n_obl = v.len();
    assert!(n_obl == c_hat_v.len() && n_obl == r_A.len() && n_obl == rho_i.len());
    for i in 0..n_obl {
        assert!(v[i].len() == pp.m && v[i][0].len() == pp.n && v[i].len() * v[i][0].len() == pp.l);
        assert_eq!(rho_i[i].len(), pp.l);
    }

    // Per-obligation challenge-integrity step (independent across obligations)
    let pieces: Vec<(
        Vec<G1Element>,
        G1Element,
        G1Element,
        Vec<i64>,
        Vec<blstrs::Scalar>,
    )> = (0..n_obl)
        .into_par_iter()
        .map(|i| {
            //Flatten v_i into single vector
            let mut v_flat_i64: Vec<i64> = Vec::with_capacity(pp.l);
            let mut v_flat_bls: Vec<bls::ZpElement> = Vec::with_capacity(pp.l);
            for k in 0..v[i].len() {
                for j in 0..v[i][k].len() {
                    v_flat_i64.push(v[i][k][j]);
                    let v_ij = blstrs::Scalar::from(v[i][k][j] as u64);
                    v_flat_bls.push(blstrs_to_bls_field_elem(&v_ij));
                }
            }

            let rho_i_bls: Vec<bls::ZpElement> = rho_i[i]
                .iter()
                .map(|r| blstrs_to_bls_field_elem(r))
                .collect();

            //Calculate c_i (commitments to elements v_i)
            let mut c_i_i: Vec<bls::G1Element> = Vec::with_capacity(pp.l);
            for (idx, v_i) in v_flat_bls.iter().enumerate() {
                c_i_i.push((pp.g_bls * *v_i) + (pp.h * rho_i_bls[idx]));
            }

            //Get per-obligation challenge z
            let mut challenge_transcript = TranSeq::new();
            challenge_transcript.push(TranElem::Gt(c_hat_v[i]));
            for idx in 0..pp.l {
                challenge_transcript.push(TranElem::G1(c_i_i[idx]));
            }
            let z = challenge_transcript.gen_challenge();

            //Calculate powers of z
            let mut z_i: Vec<ZpElement> = Vec::with_capacity(pp.l);
            for j in 0..pp.m {
                for k in 0..pp.n {
                    z_i.push(z.pow(((j + 1) + (pp.zk_matrix_srs.q * (k + 1))) as u64));
                }
            }

            let mut rho_z: ZpElement = ZpElement::from(0u64);
            for idx in 0..pp.l {
                rho_z += rho_i_bls[idx] * z_i[idx];
            }

            let f_z = f_of_x(z, pp.m, pp.n, pp.zk_matrix_srs.q, &v_flat_bls);
            let mu = ZpElement::rand();
            let q_alpha = q_of_x(alpha, z, pp.m, pp.n, pp.zk_matrix_srs.q, &v_flat_bls);
            let pi_eq_i = (pp.g_bls * q_alpha) + (pp.h * mu);
            let theta_i = pp.g_bls * (r_A[i] - rho_z - (mu * (alpha - z)));

            (c_i_i, pi_eq_i, theta_i, v_flat_i64, rho_i[i].clone())
        })
        .collect();

    let mut c_i: Vec<Vec<G1Element>> = Vec::with_capacity(n_obl);
    let mut pi_eq: Vec<G1Element> = Vec::with_capacity(n_obl);
    let mut theta: Vec<G1Element> = Vec::with_capacity(n_obl);
    let mut all_vals: Vec<i64> = Vec::with_capacity(n_obl * pp.l);
    let mut all_lower_blindings: Vec<blstrs::Scalar> = Vec::with_capacity(n_obl * pp.l);
    for (c_i_i, pi_eq_i, theta_i, v_flat_i64, rho_i_i) in pieces {
        c_i.push(c_i_i);
        pi_eq.push(pi_eq_i);
        theta.push(theta_i);
        all_vals.extend_from_slice(&v_flat_i64);
        all_lower_blindings.extend_from_slice(&rho_i_i);
    }
    println!("zkrp prove multi: proved equalities");

    // Aggregated range proof over all obligations' elements
    let mut all_upper_blindings: Vec<blstrs::Scalar> =
        all_lower_blindings.iter().map(|rho| -rho).collect();

    // Pad values and blindings to a power of two (bls_bulletproofs requires m = power of two)
    let m_padded = (n_obl * pp.l).next_power_of_two();
    all_vals.resize(m_padded, 0);
    while all_lower_blindings.len() < m_padded {
        all_lower_blindings.push(bls_field_elem_to_blstrs_scalar(&ZpElement::from(0u64)));
    }
    while all_upper_blindings.len() < m_padded {
        all_upper_blindings.push(bls_field_elem_to_blstrs_scalar(&ZpElement::from(0u64)));
    }

    let mut bp_transcript = Transcript::new(b"v_range_proof");
    let v_range_proof = ArbitraryUpperRangeProof::prove_multiple(
        &pp.pc_gens,
        &pp.bp_gens,
        &mut bp_transcript,
        &all_vals,
        b,
        &all_lower_blindings,
        &all_upper_blindings,
        32,
    )
    .expect("Error proving ranges of v ");
    println!("zkrp prove multi: proved range proof");

    ZKRPMultiProof {
        c_i,
        pi_eq,
        theta,
        v_range_proof,
    }
}

pub fn prove(
    pp: &ZKRPParams,
    v: &Vec<Vec<i64>>,
    c_hat_v: GtElement,
    r_A: ZpElement,
    b: u32,
    alpha: ZpElement,
    rho_i_blstrs: &[blstrs::Scalar],
) -> ZKRPProof {
    assert!(v.len() == pp.m && v[0].len() == pp.n && v.len() * v[0].len() == pp.m * pp.n);
    assert_eq!(rho_i_blstrs.len(), pp.l);

    //Flatten v into single vector row-by-row
    let mut v_flat_i64: Vec<i64> = Vec::with_capacity(pp.l);
    let mut v_flat_bls: Vec<bls::ZpElement> = Vec::with_capacity(pp.l);
    for j in 0..v.len() {
        for k in 0..v[j].len() {
            v_flat_i64.push(v[j][k]);
            let v_i = blstrs::Scalar::from(v[j][k] as u64);
            v_flat_bls.push(blstrs_to_bls_field_elem(&v_i));
        }
    }

    let rho_i_bls: Vec<bls::ZpElement> = rho_i_blstrs
        .iter()
        .map(|r| blstrs_to_bls_field_elem(r))
        .collect();

    //Calculate c_i (commitments to elements v_i)
    let mut c_i: Vec<bls::G1Element> = Vec::with_capacity(pp.l);
    for (i, v_i) in v_flat_bls.iter().enumerate() {
        c_i.push((pp.g_bls * *v_i) + (pp.h * rho_i_bls[i]));
    }

    //Get challenge z
    let mut challenge_transcript = TranSeq::new();
    challenge_transcript.push(TranElem::Gt(c_hat_v));
    for i in 0..pp.l {
        challenge_transcript.push(TranElem::G1(c_i[i]));
    }
    let z = challenge_transcript.gen_challenge();

    //Calculate powers of z
    let mut z_i: Vec<ZpElement> = Vec::with_capacity(pp.l);
    for i in 0..pp.m {
        for j in 0..pp.n {
            z_i.push(z.pow(((i + 1) + (pp.zk_matrix_srs.q * (j + 1))) as u64));
        }
    }

    let mut rho_z: ZpElement = ZpElement::from(0u64);
    for i in 0..pp.l {
        rho_z += rho_i_bls[i] * z_i[i];
    }

    let f_z = f_of_x(z, pp.m, pp.n, pp.zk_matrix_srs.q, &v_flat_bls);
    let c = (pp.g_bls * f_z) + (pp.h * rho_z);

    let mu = ZpElement::rand();
    let q_alpha = q_of_x(alpha, z, pp.m, pp.n, pp.zk_matrix_srs.q, &v_flat_bls);
    let pi_eq = (pp.g_bls * q_alpha) + (pp.h * mu);
    let theta = pp.g_bls * (r_A - rho_z - (mu * (alpha - z)));

    //Bulletproofs
    let v_range_proof: ArbitraryUpperRangeProof;
    let mut bp_transcript = Transcript::new(b"v_range_proof");
    let neg_rho_i_blstrs: Vec<blstrs::Scalar> = rho_i_blstrs.iter().map(|rho| -rho).collect();
    if pp.l == 1 {
        v_range_proof = ArbitraryUpperRangeProof::prove_single(
            &pp.pc_gens,
            &pp.bp_gens,
            &mut bp_transcript,
            v_flat_i64[0],
            b,
            &rho_i_blstrs[0],
            &neg_rho_i_blstrs[0],
            32,
        )
        .expect("Error proving range of v ");
    } else {
        v_range_proof = ArbitraryUpperRangeProof::prove_multiple(
            &pp.pc_gens,
            &pp.bp_gens,
            &mut bp_transcript,
            &v_flat_i64,
            b,
            rho_i_blstrs,
            &neg_rho_i_blstrs,
            32,
        )
        .expect("Error proving range of v ");
    }

    return ZKRPProof {
        c_i,
        pi_eq,
        theta,
        v_range_proof,
    };
}

pub fn f_of_x(x: ZpElement, m: usize, n: usize, q: usize, v_i: &Vec<ZpElement>) -> ZpElement {
    let mut sum = ZpElement::from(0u64);
    for i in 0..m {
        for j in 0..n {
            sum += v_i[(n * i) + j] * x.pow(((i + 1) + (q * (j + 1))) as u64);
        }
    }
    return sum;
}

pub fn q_of_x(
    x: ZpElement,
    z: ZpElement,
    m: usize,
    n: usize,
    q: usize,
    v_i: &Vec<ZpElement>,
) -> ZpElement {
    let f_x = f_of_x(x, m, n, q, v_i);
    let f_z = f_of_x(z, m, n, q, v_i);
    let numerator = f_x - f_z;
    let denominator = x - z;
    return numerator * denominator.inv();
}

impl ZKRPProof {
    pub fn verify(&self, pp: &ZKRPParams, c_hat_v: GtElement, b: u32) -> bool {
        //Get challenge z
        let mut challenge_transcript = TranSeq::new();
        challenge_transcript.push(TranElem::Gt(c_hat_v));
        for i in 0..pp.l {
            challenge_transcript.push(TranElem::G1(self.c_i[i]));
        }
        let z = challenge_transcript.gen_challenge();

        //Calculate powers of z
        let mut z_i: Vec<ZpElement> = Vec::with_capacity(pp.l);
        for i in 0..pp.m {
            for j in 0..pp.n {
                z_i.push(z.pow(((i + 1) + (pp.zk_matrix_srs.q * (j + 1))) as u64));
            }
        }

        let mut c: G1Element = get_bls_g1_zero();
        for i in 0..pp.m {
            for j in 0..pp.n {
                let idx = j + (i * pp.n);
                c += self.c_i[idx] * z_i[idx];
            }
        }

        let lhs = c_hat_v - (c * pp.g_prime);

        let g_prime_alpha_minus_z = pp.g_prime_alpha - (pp.g_prime * z);
        let rhs = (self.pi_eq * g_prime_alpha_minus_z) + (self.theta * pp.h_prime);

        // assert!(lhs == rhs);
        if lhs != rhs {
            println!("Failed equality check");
            return false;
        }

        //Check range proof on v
        let v_range_verified: bool;
        let mut verif_transcript = Transcript::new(b"v_range_proof");
        if pp.l == 1 {
            v_range_verified = self
                .v_range_proof
                .verify_single(&pp.pc_gens, &pp.bp_gens, &mut verif_transcript, 32)
                .expect("Error verifying range of v ");
        } else {
            v_range_verified = self
                .v_range_proof
                .verify_multiple(&pp.pc_gens, &pp.bp_gens, &mut verif_transcript, 32)
                .expect("Error verifying ranges of v ");
        }
        if !v_range_verified {
            println!("Error verifying ranges of v ");
            return false;
        }

        //Check equality of commitments with range proofs!
        let mut equality_comms_verified = true;
        let g_pow_b = pp.g_bls * ZpElement::from(b as u64);
        for i in 0..pp.l {
            equality_comms_verified &=
                (self.c_i[i] == blstrs_affine_to_bls_g1(&self.v_range_proof.lower_comms[i].into()));
            equality_comms_verified &= ((g_pow_b - self.c_i[i])
                == blstrs_affine_to_bls_g1(&self.v_range_proof.upper_comms[i].into()));
        }
        if !equality_comms_verified {
            println!("Error showing equality of commitments with c_i");
            return false;
        }

        return true;
    }
}

impl ZKRPMultiProof {
    pub fn verify_multi(&self, pp: &ZKRPParams, c_hat_v: &[GtElement], b: u32) -> bool {
        let n_obl = self.c_i.len();
        assert!(n_obl == c_hat_v.len() && n_obl == self.pi_eq.len() && n_obl == self.theta.len());

        // Per-obligation challenge-integrity step (independent across obligations)
        let equalities_ok = (0..n_obl).into_par_iter().all(|i| {
            //Get per-obligation challenge z
            let mut challenge_transcript = TranSeq::new();
            challenge_transcript.push(TranElem::Gt(c_hat_v[i]));
            for idx in 0..pp.l {
                challenge_transcript.push(TranElem::G1(self.c_i[i][idx]));
            }
            let z = challenge_transcript.gen_challenge();

            //Calculate powers of z
            let mut z_i: Vec<ZpElement> = Vec::with_capacity(pp.l);
            for j in 0..pp.m {
                for k in 0..pp.n {
                    z_i.push(z.pow(((j + 1) + (pp.zk_matrix_srs.q * (k + 1))) as u64));
                }
            }

            let mut c: G1Element = get_bls_g1_zero();
            for idx in 0..pp.l {
                c += self.c_i[i][idx] * z_i[idx];
            }

            let lhs = c_hat_v[i] - (c * pp.g_prime);

            let g_prime_alpha_minus_z = pp.g_prime_alpha - (pp.g_prime * z);
            let rhs = (self.pi_eq[i] * g_prime_alpha_minus_z) + (self.theta[i] * pp.h_prime);

            lhs == rhs
        });
        if !equalities_ok {
            println!("Failed equality check");
            return false;
        }
        println!("zkrp verify multi: verified equalities");

        //Check aggregated range proof on all v
        let mut verif_transcript = Transcript::new(b"v_range_proof");
        let v_range_verified = self
            .v_range_proof
            .verify_multiple(&pp.pc_gens, &pp.bp_gens, &mut verif_transcript, 32)
            .expect("Error verifying ranges of v ");
        if !v_range_verified {
            println!("Error verifying ranges of v ");
            return false;
        }
        println!("zkrp verify multi: verified range proof");

        //Check equality of commitments with range proofs (skip padding tail)
        let g_pow_b = pp.g_bls * ZpElement::from(b as u64);
        let total = n_obl * pp.l;
        let comms_ok = (0..total).into_par_iter().all(|k| {
            let i = k / pp.l;
            let idx = k % pp.l;
            (self.c_i[i][idx] == blstrs_affine_to_bls_g1(&self.v_range_proof.lower_comms[k].into()))
                && ((g_pow_b - self.c_i[i][idx])
                    == blstrs_affine_to_bls_g1(&self.v_range_proof.upper_comms[k].into()))
        });
        if !comms_ok {
            println!("Error showing equality of commitments with c_i");
            return false;
        }
        println!("zkrp verify multi: verified comms");

        return true;
    }
}
