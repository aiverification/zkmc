use bls_bulletproofs::group::ff::Field;
use criterion::{Criterion, criterion_group, criterion_main};
use rand::SeedableRng;
use rand_chacha::ChaChaRng;
use rayon::prelude::*;
use serde::Deserialize;
use serde_json::Value;
use std::{collections::HashMap, fs, fs::File, io::Write, path::Path, time::Instant};
use dashmap::DashMap;
use zkmatrix::commit_mat::CommitMat;
use zkmatrix::utils::curve as bls;
use zkmatrix::utils::fiat_shamir::TranSeq;
use zkmatrix::zkprotocols::{zk_matmul::ZkMatMul, zk_trans::ZkTranSeqProver};
use zkmc_symbolic::zkp_cache::*;
use zkmc_symbolic::{utils::*, zkrp, zkrp::*};

type MatrixCacheKey = (Vec<Vec<i64>>, usize);

use bls_bulletproofs::{BulletproofGens, PedersenGens};

#[derive(Debug, Deserialize, Clone)]
struct InputParams {
    obligations: Vec<Obligation>,
    #[serde(flatten)]
    others: HashMap<String, Value>,
}

#[derive(Debug, Deserialize, Clone)]
struct Obligation {
    obligation_type: String,
    matrices: Matrices,
    witness: Witness,
    computed_values: ComputedValues,
    #[serde(flatten)]
    others: HashMap<String, Value>,
}

#[derive(Debug, Deserialize, Clone)]
struct Matrices {
    A_s: Vec<Vec<i64>>,
    b_s: Vec<Vec<i64>>,
    G_p: Vec<Vec<i64>>,
    h_p: Vec<Vec<i64>>,
    #[serde(flatten)]
    others: HashMap<String, Value>,
}

#[derive(Debug, Deserialize, Clone)]
struct Witness {
    lambda_s: Vec<Vec<i64>>,
    mu_s: Vec<Vec<i64>>,
    #[serde(flatten)]
    others: HashMap<String, Value>,
}

#[derive(Debug, Deserialize, Clone)]
struct ComputedValues {
    neg_b_s_T_lambda_s: i64,
    neg_h_p_T_mu_s: i64,
    A_s_T_lambda_s: Vec<Vec<i64>>,
    G_p_T_mu_s: Vec<Vec<i64>>,
    #[serde(flatten)]
    others: HashMap<String, Value>,
}

#[derive(Debug, Clone)]
struct ACacheEntry {
    A_r: bls::ZpElement,
    A_comm: bls::GtElement,
    A_cache: Vec<bls::G2Element>,
    A_blind: bls::GtElement,
    A_plus_M_zkrp: ZKRPProof,
    q: usize,
}

#[derive(Debug, Clone)]
struct BCacheEntry {
    b_r: bls::ZpElement,
    b_comm: bls::GtElement,
    b_cache: Vec<bls::G2Element>,
    b_blind: bls::GtElement,
    neg_b_plus_M_zkrp: ZKRPProof,
    q: usize,
}

#[derive(Debug, Clone)]
struct LambdaCacheEntry {
    lambda_r: bls::ZpElement,
    lambda_comm: bls::GtElement,
    lambda_cache: Vec<bls::G1Element>,
    lambda_blind: bls::GtElement,
    lambda_zkrp: ZKRPProof,
}

#[derive(Debug, Clone)]
struct MuCacheEntry {
    mu_r: bls::ZpElement,
    mu_comm: bls::GtElement,
    mu_cache: Vec<bls::G1Element>,
    mu_blind: bls::GtElement,
    mu_zkrp: ZKRPProof,
}

#[derive(Debug, Clone)]
struct E1CacheEntry {
    e1_r: bls::ZpElement,
    e1_blind: bls::GtElement,
    zkmm_proof: TranSeq,
}

#[derive(Debug, Clone)]
struct E2CacheEntry {
    e2_r: bls::ZpElement,
    e2_blind: bls::GtElement,
    zkmm_proof: TranSeq,
}

#[derive(Debug, Clone)]
struct E3CacheEntry {
    e3_blind: bls::GtElement,
    h_r: bls::ZpElement,
}

fn prove_and_verify_benchmarks_full_cache_cached(c: &mut Criterion) {
    let timeout_ms: u128 = 2 * 60 * 60 * 1000;
    let chunk_size = 200;
    let sample_size: usize = 1;
    let path_to_file_g = "input/".to_string();
    let candidate_files = vec![
        "test.json".to_string(),
        // "exb_i4a2.json".to_string(),
        // "rr_2.json".to_string(),
        // "dhcp_noOFF_7_2_7.json".to_string(),
    ];

    for input_file in candidate_files {
        let log_string = "output/bench_".to_string() + &input_file.clone() + ".log";
        let path = Path::new(&log_string);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let mut log_file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .unwrap();

        for sample in 0..sample_size {
            println!("================ Reading data from file ================");
            let input_str = path_to_file_g.clone() + &input_file.clone();
            let file = File::open(input_str.clone()).expect("Error opening file ");
            let input: InputParams = serde_json::from_reader(file).expect("Failed to parse JSON ");
            println!("No. obligations: {:?}", input.obligations.len());
            let total_setup_time: u128;
            let mut total_prove_time: u128 = 0;
            let mut total_verify_time: u128 = 0;

            let big_M = 2u32.pow(31) - 1;

            // ========== PHASE 0: Scan obligations for unique keys ==========
            println!("================ Phase 0: Scanning obligations ================");
            let mut max_q: usize = 0;
            let mut unique_A_matrices: HashMap<MatrixCacheKey, Vec<Vec<i64>>> = HashMap::new();
            let mut unique_b_matrices: HashMap<MatrixCacheKey, Vec<Vec<i64>>> = HashMap::new();
            let mut unique_lambda: HashMap<Vec<Vec<i64>>, Vec<Vec<i64>>> = HashMap::new();
            let mut unique_mu: HashMap<Vec<Vec<i64>>, Vec<Vec<i64>>> = HashMap::new();
            let mut unique_e1_keys: HashMap<(Vec<Vec<i64>>, Vec<Vec<i64>>, usize), Vec<Vec<i64>>> = HashMap::new();
            let mut unique_e2_keys: HashMap<(Vec<Vec<i64>>, Vec<Vec<i64>>, usize), i64> = HashMap::new();
            let mut unique_e3_keys: HashMap<(Vec<Vec<i64>>, Vec<Vec<i64>>), i64> = HashMap::new();

            for obligation in input.obligations.iter() {
                let A_s_T = pad_matrix(&transpose_matrix(&obligation.matrices.A_s));
                let neg_b_s_T =
                    pad_matrix(&transpose_matrix(&negate_matrix(&obligation.matrices.b_s)));
                let G_p_T = pad_matrix(&transpose_matrix(&obligation.matrices.G_p));
                let h_p_T = pad_matrix(&transpose_matrix(&obligation.matrices.h_p));
                let lambda_s = pad_matrix(&obligation.witness.lambda_s);
                let mu_s = pad_matrix(&obligation.witness.mu_s);
                let e1_padded = pad_matrix(&obligation.computed_values.A_s_T_lambda_s);
                let e2_scalar = obligation.computed_values.neg_b_s_T_lambda_s;
                let e3_scalar = obligation.computed_values.neg_h_p_T_mu_s;

                max_q = max_q
                    .max(A_s_T.len())
                    .max(A_s_T[0].len())
                    .max(G_p_T.len())
                    .max(G_p_T[0].len());

                let n = A_s_T[0].len();
                let n_prime = G_p_T[0].len();
                let max_n = if n >= n_prime { n } else { n_prime };
                let q = max_n + 1;

                unique_A_matrices.entry((A_s_T.clone(), q)).or_insert(A_s_T.clone());
                unique_b_matrices.entry((neg_b_s_T.clone(), q)).or_insert(neg_b_s_T.clone());
                unique_lambda.entry(lambda_s.clone()).or_insert(lambda_s.clone());
                unique_mu.entry(mu_s.clone()).or_insert(mu_s.clone());
                unique_e1_keys.entry((A_s_T.clone(), lambda_s.clone(), q)).or_insert(e1_padded);
                unique_e2_keys.entry((neg_b_s_T.clone(), lambda_s.clone(), q)).or_insert(e2_scalar);
                unique_e3_keys.entry((h_p_T, mu_s)).or_insert(e3_scalar);
            }
            println!("max_q: {max_q}");

            println!(
                "Unique A: {}, b: {}, lambda: {}, mu: {}, e1: {}, e2: {}, e3: {}",
                unique_A_matrices.len(),
                unique_b_matrices.len(),
                unique_lambda.len(),
                unique_mu.len(),
                unique_e1_keys.len(),
                unique_e2_keys.len(),
                unique_e3_keys.len(),
            );

            // ========== PHASE 1: SRS Setup ==========
            println!("================ Phase 1: SRS Setup ================");
            let setup_timer = Instant::now();

            let first_obl = &input.obligations[0];
            let first_A_s_T = pad_matrix(&transpose_matrix(&first_obl.matrices.A_s));
            let first_neg_b_s_T =
                pad_matrix(&transpose_matrix(&negate_matrix(&first_obl.matrices.b_s)));
            let first_lambda_s = pad_matrix(&first_obl.witness.lambda_s);
            let first_e_1 = pad_matrix(&first_obl.computed_values.A_s_T_lambda_s);
            let first_e_2 = vec![vec![first_obl.computed_values.neg_b_s_T_lambda_s]];

            let mut pc_gens = PedersenGens::default();
            let bp_gens = BulletproofGens::new(64, (max_q + 1).pow(2) * 2);
            let g_blstrs: blstrs::G1Affine = pc_gens.B.into();
            let g_bls = blstrs_affine_to_bls_g1(&g_blstrs);

            let (throwaway_srs, alpha) =
                zkmatrix::setup::SRS::new_with_chosen_g_return_s_hat(32, g_bls);
            let g_prime = throwaway_srs.h_hat.clone();

            let A_lambda_e1_dims = ZkMatMulDimsCached::new(&first_A_s_T, &first_lambda_s, &first_e_1);
            let b_lambda_e2_dims = ZkMatMulDimsCached::new(&first_neg_b_s_T, &first_lambda_s, &first_e_2);

            let zkp_pp = construct_zkp_srs_cached(
                max_q,
                1usize, 1usize, 1usize,
                g_blstrs, g_bls, g_prime, alpha,
                A_lambda_e1_dims, b_lambda_e2_dims,
                pc_gens, bp_gens, big_M,
            );

            total_setup_time = setup_timer.elapsed().as_millis();
            println!("Phase 1 complete. Setup time: {}ms", total_setup_time);

            if total_setup_time > timeout_ms {
                writeln!(
                    log_file,
                    "Sample {} - setup: {}ms - TIMED_OUT",
                    sample, total_setup_time
                ).unwrap();
                break;
            }

            // ========== PHASE 2: Prover (Cache Population) ==========
            println!("================ Phase 2: Prover ================");
            let prove_timer = Instant::now();

            let mut prover_A_cache: HashMap<MatrixCacheKey, ACacheEntry> = HashMap::new();
            let mut prover_b_cache: HashMap<MatrixCacheKey, BCacheEntry> = HashMap::new();
            let mut prover_lambda_cache: HashMap<Vec<Vec<i64>>, LambdaCacheEntry> = HashMap::new();
            let mut prover_mu_cache: HashMap<Vec<Vec<i64>>, MuCacheEntry> = HashMap::new();
            let mut prover_e1_cache: HashMap<(Vec<Vec<i64>>, Vec<Vec<i64>>, usize), E1CacheEntry> = HashMap::new();
            let mut prover_e2_cache: HashMap<(Vec<Vec<i64>>, Vec<Vec<i64>>, usize), E2CacheEntry> = HashMap::new();
            let mut prover_e3_cache: HashMap<(Vec<Vec<i64>>, Vec<Vec<i64>>), E3CacheEntry> = HashMap::new();

            // --- A caches ---
            println!("Pre-computing {} unique A matrix proofs...", unique_A_matrices.len());
            let a_results: Vec<_> = unique_A_matrices
                .par_iter()
                .map(|((A_s_T, q), _)| {
                    let temp_A_r = bls::ZpElement::rand();
                    let (temp_A_comm, temp_A_cache) =
                        vec_mat_to_zkmatrix_i64("A_s^T".to_string(), &A_s_T)
                            .commit_rm(&zkp_pp.zk_matrix_srs);
                    let temp_A_blind = temp_A_comm + (temp_A_r * zkp_pp.zk_matrix_srs.blind_base);

                    let mat_m = A_s_T.len();
                    let mat_n = A_s_T[0].len();
                    let mut A_plus_M: Vec<Vec<i64>> = vec![vec![zkp_pp.big_M as i64; mat_n]; mat_m];
                    for j in 0..mat_m {
                        for k in 0..mat_n {
                            A_plus_M[j][k] += A_s_T[j][k];
                        }
                    }

                    let A_plus_M_l = mat_m * mat_n;
                    let mut A_plus_M_g_i = zkp_pp.g_i_vec[0..2 * A_plus_M_l].to_vec();
                    A_plus_M_g_i[A_plus_M_l] = get_bls_g1_zero();

                    let M_m_n: Vec<Vec<i64>> = vec![vec![zkp_pp.big_M as i64; mat_n]; mat_m];
                    let mat_M_m_n = vec_mat_to_zkmatrix_i64("M_m_n".to_string(), &M_m_n);
                    let (M_m_n_comm, _) = mat_M_m_n.commit_rm(&zkp_pp.zk_matrix_srs);
                    let A_prime_comm = temp_A_blind + M_m_n_comm;

                    let A_plus_M_zkrp_pp = zkrp::ZKRPParams {
                        l: A_plus_M_l, m: mat_m, n: mat_n,
                        g_blstrs: zkp_pp.g_blstrs, g_bls: zkp_pp.g_bls,
                        g_prime: zkp_pp.g_prime, g_prime_alpha: zkp_pp.g_prime_alpha,
                        g_i: zkp_pp.g_i_vec.clone(),
                        h: zkp_pp.h, h_prime: zkp_pp.h_prime,
                        zk_matrix_srs: zkp_pp.zk_matrix_srs.clone(),
                        pc_gens: zkp_pp.pc_gens.clone(), bp_gens: zkp_pp.bp_gens.clone(),
                    };
                    let A_plus_M_proof = zkrp::prove(
                        &A_plus_M_zkrp_pp, &A_plus_M, A_prime_comm, temp_A_r,
                        2 * zkp_pp.big_M, alpha,
                    );

                    (
                        (A_s_T.clone(), *q),
                        ACacheEntry {
                            A_r: temp_A_r, A_comm: temp_A_comm,
                            A_cache: temp_A_cache, A_blind: temp_A_blind,
                            A_plus_M_zkrp: A_plus_M_proof, q: *q,
                        },
                    )
                })
                .collect();
            for (key, entry) in a_results {
                prover_A_cache.insert(key, entry);
            }

            // --- b caches ---
            println!("Pre-computing {} unique b matrix proofs...", unique_b_matrices.len());
            let b_results: Vec<_> = unique_b_matrices
                .par_iter()
                .map(|((neg_b_s_T, q), _)| {
                    let temp_b_r = bls::ZpElement::rand();
                    let (temp_b_comm, temp_b_cache) =
                        vec_mat_to_zkmatrix_i64("-b_s^T".to_string(), &neg_b_s_T)
                            .commit_rm(&zkp_pp.zk_matrix_srs);
                    let temp_b_blind = temp_b_comm + (temp_b_r * zkp_pp.zk_matrix_srs.blind_base);

                    let mat_n = neg_b_s_T[0].len();
                    let mut neg_b_plus_M: Vec<Vec<i64>> = vec![vec![zkp_pp.big_M as i64; mat_n]];
                    for k in 0..mat_n {
                        neg_b_plus_M[0][k] += neg_b_s_T[0][k];
                    }

                    let neg_b_plus_M_l = mat_n;
                    let mut neg_b_plus_M_g_i = zkp_pp.g_i_vec[0..2 * neg_b_plus_M_l].to_vec();
                    neg_b_plus_M_g_i[neg_b_plus_M_l] = get_bls_g1_zero();

                    let M_1_n: Vec<Vec<i64>> = vec![vec![zkp_pp.big_M as i64; mat_n]];
                    let mat_M_1_n = vec_mat_to_zkmatrix_i64("M_1_n".to_string(), &M_1_n);
                    let (M_1_n_comm, _) = mat_M_1_n.commit_rm(&zkp_pp.zk_matrix_srs);
                    let b_prime_comm = temp_b_blind + M_1_n_comm;

                    let neg_b_plus_M_zkrp_pp = zkrp::ZKRPParams {
                        l: neg_b_plus_M_l, m: 1, n: mat_n,
                        g_blstrs: zkp_pp.g_blstrs, g_bls: zkp_pp.g_bls,
                        g_prime: zkp_pp.g_prime, g_prime_alpha: zkp_pp.g_prime_alpha,
                        g_i: zkp_pp.g_i_vec.clone(),
                        h: zkp_pp.h, h_prime: zkp_pp.h_prime,
                        zk_matrix_srs: zkp_pp.zk_matrix_srs.clone(),
                        pc_gens: zkp_pp.pc_gens.clone(), bp_gens: zkp_pp.bp_gens.clone(),
                    };
                    let neg_b_plus_M_proof = zkrp::prove(
                        &neg_b_plus_M_zkrp_pp, &neg_b_plus_M, b_prime_comm, temp_b_r,
                        2 * zkp_pp.big_M, alpha,
                    );

                    (
                        (neg_b_s_T.clone(), *q),
                        BCacheEntry {
                            b_r: temp_b_r, b_comm: temp_b_comm,
                            b_cache: temp_b_cache, b_blind: temp_b_blind,
                            neg_b_plus_M_zkrp: neg_b_plus_M_proof, q: *q,
                        },
                    )
                })
                .collect();
            for (key, entry) in b_results {
                prover_b_cache.insert(key, entry);
            }

            // --- lambda caches ---
            println!("Pre-computing {} unique lambda proofs...", unique_lambda.len());
            let lambda_results: Vec<_> = unique_lambda
                .par_iter()
                .map(|(lambda_s, _)| {
                    let temp_lambda_r = bls::ZpElement::rand();
                    let (temp_lambda_comm, temp_lambda_cache) =
                        vec_mat_to_zkmatrix_i64("lambda_s".to_string(), &lambda_s)
                            .commit_cm(&zkp_pp.zk_matrix_srs);
                    let temp_lambda_blind = temp_lambda_comm
                        + (temp_lambda_r * zkp_pp.zk_matrix_srs.blind_base);

                    let lambda_l = lambda_s.len() * lambda_s[0].len();
                    let mut lambda_g_i = zkp_pp.g_i_vec[0..2 * lambda_l].to_vec();
                    lambda_g_i[lambda_l] = get_bls_g1_zero();
                    let lambda_zkrp_pp = zkrp::ZKRPParams {
                        l: lambda_l, m: lambda_s.len(), n: lambda_s[0].len(),
                        g_blstrs: zkp_pp.g_blstrs, g_bls: zkp_pp.g_bls,
                        g_prime: zkp_pp.g_prime, g_prime_alpha: zkp_pp.g_prime_alpha,
                        g_i: zkp_pp.g_i_vec.clone(),
                        h: zkp_pp.h, h_prime: zkp_pp.h_prime,
                        zk_matrix_srs: zkp_pp.zk_matrix_srs.clone(),
                        pc_gens: zkp_pp.pc_gens.clone(), bp_gens: zkp_pp.bp_gens.clone(),
                    };
                    let lambda_zkrp_proof = zkrp::prove(
                        &lambda_zkrp_pp, lambda_s, temp_lambda_blind, temp_lambda_r,
                        zkp_pp.big_M, alpha,
                    );

                    (
                        lambda_s.clone(),
                        LambdaCacheEntry {
                            lambda_r: temp_lambda_r,
                            lambda_comm: temp_lambda_comm,
                            lambda_cache: temp_lambda_cache,
                            lambda_blind: temp_lambda_blind,
                            lambda_zkrp: lambda_zkrp_proof,
                        },
                    )
                })
                .collect();
            for (key, entry) in lambda_results {
                prover_lambda_cache.insert(key, entry);
            }

            // --- mu caches ---
            println!("Pre-computing {} unique mu proofs...", unique_mu.len());
            let mu_results: Vec<_> = unique_mu
                .par_iter()
                .map(|(mu_s, _)| {
                    let temp_mu_r = bls::ZpElement::rand();
                    let (temp_mu_comm, temp_mu_cache) =
                        vec_mat_to_zkmatrix_i64("mu_s".to_string(), &mu_s)
                            .commit_cm(&zkp_pp.zk_matrix_srs);
                    let temp_mu_blind = temp_mu_comm
                        + (temp_mu_r * zkp_pp.zk_matrix_srs.blind_base);

                    let mu_l = mu_s.len() * mu_s[0].len();
                    let mut mu_g_i = zkp_pp.g_i_vec[0..2 * mu_l].to_vec();
                    mu_g_i[mu_l] = get_bls_g1_zero();
                    let mu_zkrp_pp = zkrp::ZKRPParams {
                        l: mu_l, m: mu_s.len(), n: mu_s[0].len(),
                        g_blstrs: zkp_pp.g_blstrs, g_bls: zkp_pp.g_bls,
                        g_prime: zkp_pp.g_prime, g_prime_alpha: zkp_pp.g_prime_alpha,
                        g_i: zkp_pp.g_i_vec.clone(),
                        h: zkp_pp.h, h_prime: zkp_pp.h_prime,
                        zk_matrix_srs: zkp_pp.zk_matrix_srs.clone(),
                        pc_gens: zkp_pp.pc_gens.clone(), bp_gens: zkp_pp.bp_gens.clone(),
                    };
                    let mu_zkrp_proof = zkrp::prove(
                        &mu_zkrp_pp, mu_s, temp_mu_blind, temp_mu_r,
                        zkp_pp.big_M, alpha,
                    );

                    (
                        mu_s.clone(),
                        MuCacheEntry {
                            mu_r: temp_mu_r,
                            mu_comm: temp_mu_comm,
                            mu_cache: temp_mu_cache,
                            mu_blind: temp_mu_blind,
                            mu_zkrp: mu_zkrp_proof,
                        },
                    )
                })
                .collect();
            for (key, entry) in mu_results {
                prover_mu_cache.insert(key, entry);
            }

            // --- e1 caches (A_s^T * lambda_s, zkmm proof) ---
            println!("Pre-computing {} unique e1 proofs...", unique_e1_keys.len());
            let e1_results: Vec<_> = unique_e1_keys
                .par_iter()
                .map(|((A_s_T, lambda_s, q), e1_padded)| {
                    let a = prover_A_cache.get(&(A_s_T.clone(), *q)).unwrap();
                    let l = prover_lambda_cache.get(lambda_s).unwrap();

                    let mat_A = vec_mat_to_zkmatrix_i64("A_s^T".to_string(), &A_s_T);
                    let mat_lambda = vec_mat_to_zkmatrix_i64("lambda_s".to_string(), &lambda_s);
                    let mat_e1 = vec_mat_to_zkmatrix_i128("e_1".to_string(), &e1_padded);

                    let e1_r = a.A_r + l.lambda_r;
                    let (e1_comm, e1_cache) = mat_e1.commit_cm(&zkp_pp.zk_matrix_srs);
                    let e1_blind = e1_comm + (e1_r * zkp_pp.zk_matrix_srs.blind_base);

                    let protocol = ZkMatMul::new(
                        e1_blind, a.A_blind, l.lambda_blind,
                        mat_e1.shape.0, mat_e1.shape.1, mat_A.shape.1,
                    );
                    let mut prover = ZkTranSeqProver::new(&zkp_pp.zk_matrix_srs);
                    protocol.prove::<i128, i64, i64>(
                        &zkp_pp.zk_matrix_srs, &mut prover,
                        mat_e1, mat_A, mat_lambda,
                        &e1_cache, &a.A_cache, &l.lambda_cache,
                        e1_r, a.A_r, l.lambda_r,
                    );
                    let zkmm_proof = prover.publish_trans();

                    (
                        (A_s_T.clone(), lambda_s.clone(), *q),
                        E1CacheEntry { e1_r, e1_blind, zkmm_proof },
                    )
                })
                .collect();
            for (key, entry) in e1_results {
                prover_e1_cache.insert(key, entry);
            }

            // --- e2 caches (-b_s^T * lambda_s, zkmm proof) ---
            println!("Pre-computing {} unique e2 proofs...", unique_e2_keys.len());
            let e2_results: Vec<_> = unique_e2_keys
                .par_iter()
                .map(|((neg_b_s_T, lambda_s, q), e2_scalar)| {
                    let b = prover_b_cache.get(&(neg_b_s_T.clone(), *q)).unwrap();
                    let l = prover_lambda_cache.get(lambda_s).unwrap();

                    let mat_b = vec_mat_to_zkmatrix_i64("-b_s^T".to_string(), &neg_b_s_T);
                    let mat_lambda = vec_mat_to_zkmatrix_i64("lambda_s".to_string(), &lambda_s);
                    let e2_padded = vec![vec![*e2_scalar]];
                    let mat_e2 = vec_mat_to_zkmatrix_i128("e_2".to_string(), &e2_padded);

                    let e2_r = b.b_r + l.lambda_r;
                    let (e2_comm, e2_cache) = mat_e2.commit_cm(&zkp_pp.zk_matrix_srs);
                    let e2_blind = e2_comm + (e2_r * zkp_pp.zk_matrix_srs.blind_base);

                    let protocol = ZkMatMul::new(
                        e2_blind, b.b_blind, l.lambda_blind,
                        mat_e2.shape.0, mat_e2.shape.1, mat_b.shape.1,
                    );
                    let mut prover = ZkTranSeqProver::new(&zkp_pp.zk_matrix_srs);
                    protocol.prove::<i128, i64, i64>(
                        &zkp_pp.zk_matrix_srs, &mut prover,
                        mat_e2, mat_b, mat_lambda,
                        &e2_cache, &b.b_cache, &l.lambda_cache,
                        e2_r, b.b_r, l.lambda_r,
                    );
                    let zkmm_proof = prover.publish_trans();

                    (
                        (neg_b_s_T.clone(), lambda_s.clone(), *q),
                        E2CacheEntry { e2_r, e2_blind, zkmm_proof },
                    )
                })
                .collect();
            for (key, entry) in e2_results {
                prover_e2_cache.insert(key, entry);
            }

            // --- e3 caches (-h_p^T * mu_s, commitment only) ---
            println!("Pre-computing {} unique e3 commitments...", unique_e3_keys.len());
            let e3_results: Vec<_> = unique_e3_keys
                .par_iter()
                .map(|((h_p_T, mu_s), e3_scalar)| {
                    let m = prover_mu_cache.get(mu_s).unwrap();
                    let h_r = bls::ZpElement::rand();

                    let e3_padded = vec![vec![*e3_scalar]];
                    let mat_e3 = vec_mat_to_zkmatrix_i128("e_3".to_string(), &e3_padded);
                    let (e3_comm, _) = mat_e3.commit_cm(&zkp_pp.zk_matrix_srs);
                    let e3_blind = e3_comm
                        + ((h_r + m.mu_r) * zkp_pp.zk_matrix_srs.blind_base);

                    (
                        (h_p_T.clone(), mu_s.clone()),
                        E3CacheEntry { e3_blind, h_r },
                    )
                })
                .collect();
            for (key, entry) in e3_results {
                prover_e3_cache.insert(key, entry);
            }

            total_prove_time += prove_timer.elapsed().as_millis();

            // ========== PHASE 3: Chunk proofs + verification ==========
            println!("========== Chunk proofs + verification in one ==========");
            let mut timed_out = false;
            let mut all_successful = true;

            let verifier_A_cache: DashMap<(usize, HashableGtElement), ZKRPProof> = DashMap::new();
            let verifier_b_cache: DashMap<(usize, HashableGtElement), ZKRPProof> = DashMap::new();
            let verifier_lambda_cache: DashMap<HashableGtElement, ZKRPProof> = DashMap::new();
            let verifier_mu_cache: DashMap<HashableGtElement, ZKRPProof> = DashMap::new();
            let verifier_e1_cache: DashMap<(HashableGtElement, HashableGtElement, HashableGtElement), ()> = DashMap::new();
            let verifier_e2_cache: DashMap<(HashableGtElement, HashableGtElement, HashableGtElement), ()> = DashMap::new();
            let verifier_fixed_comms: DashMap<(usize, usize), PrecomputedFixedComms> = DashMap::new();

            for (chunk_idx, chunk) in input.obligations.chunks(chunk_size).enumerate() {
                if total_prove_time > timeout_ms {
                    timed_out = true;
                    break;
                }

                // --- prove ---
                let prove_timer = Instant::now();
                let proof_results: Vec<(ZkpProofCached, ZkpSRSCached, Vec<Vec<i64>>, Vec<Vec<i64>>)> =
                    chunk
                        .par_iter()
                        .enumerate()
                        .map(|(inner_idx, obligation)| {
                            let idx = chunk_idx * chunk_size + inner_idx;
                            let A_s_T = pad_matrix(&transpose_matrix(&obligation.matrices.A_s));
                            let neg_b_s_T = pad_matrix(&transpose_matrix(&negate_matrix(
                                &obligation.matrices.b_s,
                            )));
                            let G_p_T = pad_matrix(&transpose_matrix(&obligation.matrices.G_p));
                            let h_p_T = pad_matrix(&transpose_matrix(&obligation.matrices.h_p));
                            let lambda_s = pad_matrix(&obligation.witness.lambda_s);
                            let mu_s = pad_matrix(&obligation.witness.mu_s);
                            let e1_padded = pad_matrix(&obligation.computed_values.A_s_T_lambda_s);
                            let e2_scalar = obligation.computed_values.neg_b_s_T_lambda_s;
                            let e2_padded = vec![vec![e2_scalar]];

                            let m = A_s_T.len();
                            let n = A_s_T[0].len();
                            let n_prime = G_p_T[0].len();
                            let max_n = if n >= n_prime { n } else { n_prime };
                            let q = max_n + 1;

                            let a = prover_A_cache.get(&(A_s_T.clone(), q)).unwrap();
                            let b = prover_b_cache.get(&(neg_b_s_T.clone(), q)).unwrap();
                            let l = prover_lambda_cache.get(&lambda_s).unwrap();
                            let mu = prover_mu_cache.get(&mu_s).unwrap();
                            let e1 = prover_e1_cache.get(&(A_s_T.clone(), lambda_s.clone(), q)).unwrap();
                            let e2 = prover_e2_cache.get(&(neg_b_s_T.clone(), lambda_s.clone(), q)).unwrap();
                            let e3 = prover_e3_cache.get(&(h_p_T.clone(), mu_s.clone())).unwrap();

                            let A_lambda_e1_dims =
                                ZkMatMulDimsCached::new(&A_s_T, &lambda_s, &e1_padded);
                            let b_lambda_e2_dims =
                                ZkMatMulDimsCached::new(&neg_b_s_T, &lambda_s, &e2_padded);
                            let mut local_zkp_pp = zkp_pp.clone();
                            local_zkp_pp.A_lambda_e1_dims = A_lambda_e1_dims;
                            local_zkp_pp.b_lambda_e2_dims = b_lambda_e2_dims;
                            local_zkp_pp.m = m;
                            local_zkp_pp.n = n;
                            local_zkp_pp.n_prime = n_prime;

                            let pieces = CachedProverPieces {
                                A_r: a.A_r, A_blind: a.A_blind,
                                A_plus_M_zkrp: a.A_plus_M_zkrp.clone(),
                                b_r: b.b_r, b_blind: b.b_blind,
                                neg_b_plus_M_zkrp: b.neg_b_plus_M_zkrp.clone(),
                                mu_s: mu_s.clone(), mu_r: mu.mu_r,
                                mu_blind: mu.mu_blind, mu_zkrp: mu.mu_zkrp.clone(),
                                lambda_r: l.lambda_r, lambda_blind: l.lambda_blind,
                                lambda_zkrp: l.lambda_zkrp.clone(),
                                e1_r: e1.e1_r, e1_blind: e1.e1_blind,
                                e1_zkmm: e1.zkmm_proof.clone(),
                                e2_scalar,
                                e2_r: e2.e2_r, e2_blind: e2.e2_blind,
                                e2_zkmm: e2.zkmm_proof.clone(),
                                e3_scalar: obligation.computed_values.neg_h_p_T_mu_s,
                                e3_blind: e3.e3_blind, h_r: e3.h_r,
                            };
                            let zkp_proof = prove_cached(
                                &local_zkp_pp, &G_p_T, &h_p_T, &pieces, alpha,
                            );

                            println!("Obligation {} proof generated", idx + 1);

                            (zkp_proof, local_zkp_pp, G_p_T, h_p_T)
                        })
                        .collect::<Vec<_>>();
                total_prove_time += prove_timer.elapsed().as_millis();

                if total_prove_time > timeout_ms {
                    timed_out = true;
                    break;
                }

                // --- verify ---
                let verify_timer = Instant::now();
                let verify_results: Vec<bool> = proof_results
                    .par_iter()
                    .enumerate()
                    .map(|(inner_idx, (zkp_proof, local_zkp_pp, G_p_T, h_p_T))| {
                        let idx = chunk_idx * chunk_size + inner_idx;
                        let mut zkp_verified = false;

                        let (is_a_cached, a_mismatch) = {
                            let key = (local_zkp_pp.q, HashableGtElement(zkp_proof.c_A));
                            match verifier_A_cache.get(&key) {
                                Some(cached) if *cached == zkp_proof.A_plus_M_zkrp_proof => (true, false),
                                Some(_) => (false, true),
                                None => (false, false),
                            }
                        };
                        let (is_b_cached, b_mismatch) = {
                            let key = (local_zkp_pp.q, HashableGtElement(zkp_proof.c_b));
                            match verifier_b_cache.get(&key) {
                                Some(cached) if *cached == zkp_proof.neg_b_plus_M_zkrp_proof => (true, false),
                                Some(_) => (false, true),
                                None => (false, false),
                            }
                        };
                        let (is_lambda_cached, lambda_mismatch) = {
                            let key = HashableGtElement(zkp_proof.c_lambda);
                            match verifier_lambda_cache.get(&key) {
                                Some(cached) if *cached == zkp_proof.lambda_zkrp_proof => (true, false),
                                Some(_) => (false, true),
                                None => (false, false),
                            }
                        };
                        let (is_mu_cached, mu_mismatch) = {
                            let key = HashableGtElement(zkp_proof.c_mu);
                            match verifier_mu_cache.get(&key) {
                                Some(cached) if *cached == zkp_proof.mu_zkrp_proof => (true, false),
                                Some(_) => (false, true),
                                None => (false, false),
                            }
                        };
                        let (is_e1_cached, e1_mismatch) = {
                            let key = (
                                HashableGtElement(zkp_proof.c_A),
                                HashableGtElement(zkp_proof.c_lambda),
                                HashableGtElement(zkp_proof.c_e_1),
                            );
                            match verifier_e1_cache.get(&key) {
                                Some(_) => (true, false),
                                None => (false, false),
                            }
                        };
                        let (is_e2_cached, e2_mismatch) = {
                            let key = (
                                HashableGtElement(zkp_proof.c_b),
                                HashableGtElement(zkp_proof.c_lambda),
                                HashableGtElement(zkp_proof.c_e_2),
                            );
                            match verifier_e2_cache.get(&key) {
                                Some(_) => (true, false),
                                None => (false, false),
                            }
                        };

                        if a_mismatch || b_mismatch || lambda_mismatch || mu_mismatch
                            || e1_mismatch || e2_mismatch
                        {
                            zkp_verified = false;
                        } else {
                            let flags = VerifierCacheFlagsCached {
                                is_A_plus_M_cached: is_a_cached,
                                is_neg_b_plus_M_cached: is_b_cached,
                                is_lambda_cached,
                                is_mu_cached,
                                is_e1_cached,
                                is_e2_cached,
                            };
                            let fc = verifier_fixed_comms
                                .entry((local_zkp_pp.m, local_zkp_pp.n))
                                .or_insert_with(|| {
                                    let mut M_m_n: Vec<Vec<i64>> =
                                        vec![vec![big_M as i64; local_zkp_pp.n]; local_zkp_pp.m];
                                    let mat_M_m_n =
                                        vec_mat_to_zkmatrix_i64("M_m_n".to_string(), &M_m_n);
                                    let (M_m_n_comm, _) =
                                        mat_M_m_n.commit_rm(&local_zkp_pp.zk_matrix_srs);
                                    let M_1_n: Vec<Vec<i64>> =
                                        vec![vec![big_M as i64; local_zkp_pp.n]];
                                    let mat_M_1_n =
                                        vec_mat_to_zkmatrix_i64("M_1_n".to_string(), &M_1_n);
                                    let (M_1_n_comm, _) =
                                        mat_M_1_n.commit_rm(&local_zkp_pp.zk_matrix_srs);
                                    let neg_one = vec![vec![-1i64]];
                                    let mat_neg_one =
                                        vec_mat_to_zkmatrix_i128("-1".to_string(), &neg_one);
                                    let (neg_one_comm, _) =
                                        mat_neg_one.commit_cm(&local_zkp_pp.zk_matrix_srs);
                                    PrecomputedFixedComms {
                                        M_m_n_comm,
                                        M_1_n_comm,
                                        neg_one_comm,
                                    }
                                });
                            let fc_val = fc.clone();
                            drop(fc);
                            zkp_verified = zkp_proof.verify(
                                local_zkp_pp, G_p_T, h_p_T, &flags, Some(&fc_val),
                            );
                            if zkp_verified {
                                if !is_a_cached {
                                    verifier_A_cache.insert(
                                        (local_zkp_pp.q, HashableGtElement(zkp_proof.c_A)),
                                        zkp_proof.A_plus_M_zkrp_proof.clone(),
                                    );
                                }
                                if !is_b_cached {
                                    verifier_b_cache.insert(
                                        (local_zkp_pp.q, HashableGtElement(zkp_proof.c_b)),
                                        zkp_proof.neg_b_plus_M_zkrp_proof.clone(),
                                    );
                                }
                                if !is_lambda_cached {
                                    verifier_lambda_cache.insert(
                                        HashableGtElement(zkp_proof.c_lambda),
                                        zkp_proof.lambda_zkrp_proof.clone(),
                                    );
                                }
                                if !is_mu_cached {
                                    verifier_mu_cache.insert(
                                        HashableGtElement(zkp_proof.c_mu),
                                        zkp_proof.mu_zkrp_proof.clone(),
                                    );
                                }
                                if !is_e1_cached {
                                    verifier_e1_cache.insert(
                                        (
                                            HashableGtElement(zkp_proof.c_A),
                                            HashableGtElement(zkp_proof.c_lambda),
                                            HashableGtElement(zkp_proof.c_e_1),
                                        ), (),
                                    );
                                }
                                if !is_e2_cached {
                                    verifier_e2_cache.insert(
                                        (
                                            HashableGtElement(zkp_proof.c_b),
                                            HashableGtElement(zkp_proof.c_lambda),
                                            HashableGtElement(zkp_proof.c_e_2),
                                        ), (),
                                    );
                                }
                            }
                        }

                        println!("Obligation {} verified: {}", idx + 1, zkp_verified);
                        zkp_verified
                    })
                    .collect::<Vec<_>>();
                total_verify_time += verify_timer.elapsed().as_millis();

                if !verify_results.iter().all(|&v| v) {
                    all_successful = false;
                }

                println!("====== Chunk {} done ======", chunk_idx + 1);
            }

            println!("Phase 2 complete. Prove time: {}ms", total_prove_time);
            println!("Phase 3 complete. Verify time: {}ms", total_verify_time);
            println!("All verified: {}", all_successful);

            if timed_out {
                writeln!(
                    log_file,
                    "Sample {} - setup: {}ms, prove: {}ms, verify: {}ms - TIMED_OUT",
                    sample, total_setup_time, total_prove_time, total_verify_time
                ).unwrap();
                break;
            } else if all_successful {
                writeln!(
                    log_file,
                    "Sample {} - setup: {}ms, prove: {}ms, verify: {}ms - SUCCESS",
                    sample, total_setup_time, total_prove_time, total_verify_time
                ).unwrap();
            } else {
                writeln!(
                    log_file,
                    "Sample {} - setup: {}ms, prove: {}ms, verify: {}ms - VERIFICATION_FAILED",
                    sample, total_setup_time, total_prove_time, total_verify_time
                ).unwrap();
            }
        }
    }
}

pub fn construct_zkp_srs_cached(
    q: usize,
    m: usize,
    n: usize,
    n_prime: usize,
    g_blstrs: blstrs::G1Affine,
    g_bls: bls::G1Element,
    g_prime: bls::G2Element,
    alpha: bls::ZpElement,
    A_lambda_e1_dims: ZkMatMulDimsCached,
    b_lambda_e2_dims: ZkMatMulDimsCached,
    mut pc_gens: PedersenGens,
    bp_gens: BulletproofGens,
    big_M: u32,
) -> ZkpSRSCached {
    let l: usize;
    if n >= n_prime {
        l = m * n;
    } else {
        l = m * n_prime;
    }
    let mut alpha_vec: Vec<bls::ZpElement> =
        std::iter::successors(Some(alpha), |&x| Some(x * alpha))
            .take(2 * ((q.pow(2)) - 1))
            .collect();
    alpha_vec.insert(0, bls::ZpElement::from(1u64));
    let g_i_vec: Vec<bls::G1Element> = alpha_vec.par_iter().map(|&x| x * g_bls).collect();

    alpha_vec.truncate((q.pow(2) as usize) + 1);
    alpha_vec.remove(0);

    let q_alpha_vec: Vec<bls::ZpElement> = std::iter::successors(Some(alpha), |&x| Some(x * alpha))
        .take(q)
        .collect();
    let alpha_pow_q = *q_alpha_vec.last().unwrap();
    let q_i_alpha_vec: Vec<bls::ZpElement> =
        std::iter::successors(Some(alpha_pow_q), |&x| Some(x * alpha_pow_q))
            .take(q)
            .collect();

    let g_hat_j: Vec<bls::G1Element> = alpha_vec.par_iter().map(|&x| x * g_bls).collect();
    let g_hat_prime_j: Vec<bls::G1Element> = q_i_alpha_vec.par_iter().map(|&x| x * g_bls).collect();
    let g_hat_i: Vec<bls::G2Element> = q_i_alpha_vec.par_iter().map(|&x| x * g_prime).collect();
    let g_hat_prime_i: Vec<bls::G2Element> = alpha_vec.par_iter().map(|&x| x * g_prime).collect();

    let mut g_hat_mat: Vec<Vec<bls::GtElement>> = vec![];
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
    let g_prime_alpha = g_prime * alpha;

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

    return ZkpSRSCached {
        m, n, n_prime, l, q, big_M,
        g_blstrs, g_bls, g_prime, g_prime_alpha,
        g_i_vec, h, h_prime,
        g_hat_mat: g_hat_mat.clone(),
        pc_gens: pc_gens.clone(),
        bp_gens,
        zk_matrix_srs,
        A_lambda_e1_dims,
        b_lambda_e2_dims,
    };
}

criterion_group!(benches, prove_and_verify_benchmarks_full_cache_cached);
criterion_main!(benches);
