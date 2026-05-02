use bls_bulletproofs::{BulletproofGens, PedersenGens};
use group::ff::Field;
use rand::SeedableRng;
use rand_chacha::ChaChaRng;
use rayon::prelude::*;
use zkmatrix::utils::curve as bls;
use zkmatrix::{
    commit_mat::CommitMat,
    zkprotocols::{zk_matmul::ZkMatMul, zk_trans::ZkTranSeqProver},
};

pub mod range_proof;
pub mod utils;
pub mod zkmmeq;
pub mod zkp;
pub mod zkrp;
use utils::*;

fn main() {
    let A: Vec<Vec<i64>> = vec![vec![4, 5, 6], vec![100, 9, 10]];

    let A_pad = pad_matrix(&A);
    let mat_A = vec_mat_to_zkmatrix_i64("A".to_string(), &A_pad);
    println!("mxn: {:?}x{:?}", mat_A.shape.0, mat_A.shape.1);
    println!("A_pad mxn: {:?}x{:?}", A_pad.len(), A_pad[0].len());
    let l = mat_A.shape.0 * mat_A.shape.1;
    println!("l: {:?}", l);

    //Generate our initial params and whatever
    let mut pc_gens = PedersenGens::default();
    let bp_gens = BulletproofGens::new(64, (l * 2) as usize);
    let g_blstrs: blstrs::G1Affine = pc_gens.B.into();
    let g_bls = blstrs_affine_to_bls_g1(&g_blstrs);

    let (throwaway_srs, alpha) = zkmatrix::setup::SRS::new_with_chosen_g_return_s_hat(32, g_bls); //Used for g_prime
    let g_prime = throwaway_srs.h_hat.clone();

    let q = l;
    let mut alpha_vec: Vec<bls::ZpElement> =
        std::iter::successors(Some(alpha), |&x| Some(x * alpha))
            .take(2 * ((q.pow(2)) - 1))
            .collect();
    alpha_vec.insert(0, bls::ZpElement::from(1u64));
    let g_i_vec: Vec<bls::G1Element> = alpha_vec.par_iter().map(|&x| x * g_bls).collect();

    //Get alpha^x for x in [1..n] using vector calculated earlier
    alpha_vec.truncate((q.pow(2) as usize) + 1);
    alpha_vec.remove(0);

    //Get powers for g_bar_i, g_prime_bar_i
    let mut q_alpha_vec: Vec<bls::ZpElement> =
        std::iter::successors(Some(alpha), |&x| Some(x * alpha))
            .take(q)
            .collect();
    let alpha_pow_q = *q_alpha_vec.last().unwrap();
    let mut q_i_alpha_vec: Vec<bls::ZpElement> =
        std::iter::successors(Some(alpha_pow_q), |&x| Some(x * alpha_pow_q))
            .take(q)
            .collect();

    let g_hat_j: Vec<bls::G1Element> = alpha_vec.par_iter().map(|&x| x * g_bls).collect();
    let g_hat_prime_j: Vec<bls::G1Element> = q_i_alpha_vec.par_iter().map(|&x| x * g_bls).collect();
    let g_hat_i: Vec<bls::G2Element> = q_i_alpha_vec.par_iter().map(|&x| x * g_prime).collect();
    let g_hat_prime_i: Vec<bls::G2Element> = alpha_vec.par_iter().map(|&x| x * g_prime).collect();

    // Generate beta on the blstrs side so we can override pc_gens.B_blinding to match h
    // (otherwise bulletproof's lower_comms wouldn't equal our c_i = g^v * h^rho).
    let mut beta_rng = ChaChaRng::from_seed([42u8; 32]);
    let beta_blstrs = blstrs::Scalar::random(&mut beta_rng);
    let beta = blstrs_to_bls_field_elem(&beta_blstrs);
    let h_blstrs_proj: blstrs::G1Projective = pc_gens.B * beta_blstrs;
    let h = blstrs_proj_to_bls_g1(&h_blstrs_proj);
    let h_prime = g_prime * beta;
    let h_hat = h * g_prime;
    pc_gens.B_blinding = h_blstrs_proj;

    let zk_matrix_srs = zkmatrix::setup::SRS {
        q: q,
        g_hat: g_bls,
        h_hat: g_prime,
        blind_base: h_hat,
        g_hat_vec: g_hat_j.clone(),
        h_hat_vec: g_hat_i.clone(),
        g_hat_prime_vec: g_hat_prime_j.clone(),
        h_hat_prime_vec: g_hat_prime_i.clone(),
    };

    let A_r = bls::ZpElement::rand();
    let (comm_A, A_cache) = mat_A.commit_rm(&zk_matrix_srs);
    let A_blind = comm_A + (A_r * h_hat);

    let zkrp_pp = zkrp::ZKRPParams {
        l,
        m: mat_A.shape.0,
        n: mat_A.shape.1,
        g_blstrs,
        g_bls,
        g_prime,
        g_prime_alpha: g_prime * alpha,
        g_i: g_i_vec.clone(),
        h,
        h_prime,
        zk_matrix_srs: zk_matrix_srs.clone(),
        pc_gens: pc_gens.clone(),
        bp_gens: bp_gens.clone(),
    };

    let M = 2u32.pow(31) - 1;
    let zkrp_proof = zkrp::prove(&zkrp_pp, &A_pad, A_blind, A_r, M, alpha);

    let zkrp_verified = zkrp_proof.verify(&zkrp_pp, A_blind, M);
    println!("verified: {:?}", zkrp_verified);

    let b: Vec<Vec<i64>> = vec![vec![1], vec![4], vec![5]];

    let b_pad = pad_matrix(&b);
    let mat_b = vec_mat_to_zkmatrix_i64("b".to_string(), &b_pad);
    let b_r = bls::ZpElement::rand();
    let (comm_b, b_cache) = mat_b.commit_cm(&zk_matrix_srs);
    let b_blind = comm_b + (b_r * h_hat);

    let c: Vec<Vec<i64>> = vec![vec![54], vec![186]];

    let c_pad = pad_matrix(&c);
    let mat_c = vec_mat_to_zkmatrix_i128("c".to_string(), &c_pad);
    let c_r = bls::ZpElement::rand();
    let (comm_c, c_cache) = mat_c.commit_cm(&zk_matrix_srs);
    let c_blind = comm_c + (c_r * h_hat);

    println!("A: {:?}\nb: {:?}\nc: {:?}", mat_A, mat_b, mat_c);

    let A_B_C_protocol = ZkMatMul::new(
        c_blind,
        A_blind,
        b_blind,
        mat_c.shape.0,
        mat_c.shape.1,
        mat_A.shape.1,
    );
    let mut A_B_C_prover = ZkTranSeqProver::new(&zk_matrix_srs);
    A_B_C_protocol.prove::<i128, i64, i64>(
        &zk_matrix_srs,
        &mut A_B_C_prover,
        mat_c.clone(),
        mat_A.clone(),
        mat_b.clone(),
        &c_cache,
        &A_cache,
        &b_cache,
        c_r,
        A_r,
        b_r,
    );
    let A_B_C_proof = A_B_C_prover.publish_trans();
    println!("Proved a.b=c");

    let A_B_C_verifier = ZkMatMul::new(
        c_blind,
        A_blind,
        b_blind,
        mat_c.shape.0,
        mat_c.shape.1,
        mat_A.shape.1,
    );
    let A_B_C_verified = A_B_C_verifier.verify(&zk_matrix_srs, &mut A_B_C_proof.clone());
    println!("a.b=c verified: {A_B_C_verified}");
}
