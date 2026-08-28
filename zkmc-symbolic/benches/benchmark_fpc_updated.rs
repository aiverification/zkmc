/*
    Benchmark that caches A, -b for both prover and verifier. 
    Used in dataset generation for ZKMC paper.
*/

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
use zkmatrix::zkprotocols::{zk_matmul::ZkMatMul, zk_trans::ZkTranSeqProver};
use zkmc_symbolic::zkp_cache::*;
use zkmc_symbolic::{utils::*, zkrp, zkrp::*};

// Type alias for cache keys: (matrix, q)
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

fn prove_and_verify_benchmarks_full_cache(c: &mut Criterion) {
    let timeout_ms: u128 = 2 * 60 * 60 * 1000; // 2 hours
    let chunk_size = 200;
    let sample_size: usize = 1;
    let path_to_file_g = "input/".to_string();
    let candidate_files = vec![
        "test.json".to_string(),
        "exb_i4a2.json".to_string(),
        "rr_2.json".to_string(),
        "dhcp_noOFF_7_2_7.json".to_string(),
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
            let mut max_l: usize = 0;
            let mut unique_A_matrices: HashMap<MatrixCacheKey, Vec<Vec<i64>>> = HashMap::new();
            let mut unique_b_matrices: HashMap<MatrixCacheKey, Vec<Vec<i64>>> = HashMap::new();

            for obligation in input.obligations.iter() {
                let A_s_T = pad_matrix(&transpose_matrix(&obligation.matrices.A_s));
                let neg_b_s_T =
                    pad_matrix(&transpose_matrix(&negate_matrix(&obligation.matrices.b_s)));
                let G_p_T = pad_matrix(&transpose_matrix(&obligation.matrices.G_p));
                let h_p_T = pad_matrix(&transpose_matrix(&obligation.matrices.h_p));
                let lambda_s = pad_matrix(&obligation.witness.lambda_s);
                let mu_s = pad_matrix(&obligation.witness.mu_s);

                max_q = max_q
                    .max(A_s_T.len())
                    .max(A_s_T[0].len())
                    .max(G_p_T.len())
                    .max(G_p_T[0].len());

                max_l = max_l
                    .max(A_s_T.len() * A_s_T[0].len())
                    .max(neg_b_s_T.len() * neg_b_s_T[0].len())
                    .max(lambda_s.len() * lambda_s[0].len())
                    .max(mu_s.len() * mu_s[0].len());

                let n = A_s_T[0].len();
                let n_prime = G_p_T[0].len();
                let max_n = if n >= n_prime { n } else { n_prime };
                let q = max_n + 1;

                unique_A_matrices.entry((A_s_T.clone(), q)).or_insert(A_s_T.clone());
                unique_b_matrices.entry((neg_b_s_T.clone(), q)).or_insert(neg_b_s_T.clone());
            }

            println!(
                "Unique A: {}, b: {}",
                unique_A_matrices.len(),
                unique_b_matrices.len(),
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

            let pc_gens = PedersenGens::default();
            let bp_gens = BulletproofGens::new(32, max_l + 1);
            let g_blstrs: blstrs::G1Affine = pc_gens.B.into();
            let g_bls = blstrs_affine_to_bls_g1(&g_blstrs);

            let alpha = bls::ZpElement::rand();
            let g_prime = bls::G2Element::generator();

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

            // ========== PHASE 2.5a: Build verifier precompute bundle ==========
            println!("Phase 2.5a: Building verifier precompute bundle...");
            let a_verify: Vec<AVerifyEntry> = prover_A_cache
                .iter()
                .map(|((A_s_T, _q), a)| AVerifyEntry {
                    c_A: a.A_blind,
                    A_plus_M_zkrp: a.A_plus_M_zkrp.clone(),
                    m: A_s_T.len(),
                    n: A_s_T[0].len(),
                })
                .collect();
            let b_verify: Vec<BVerifyEntry> = prover_b_cache
                .iter()
                .map(|((neg_b_s_T, _q), b)| BVerifyEntry {
                    c_b: b.b_blind,
                    neg_b_plus_M_zkrp: b.neg_b_plus_M_zkrp.clone(),
                    n: neg_b_s_T[0].len(),
                })
                .collect();

            // Precompute the fixed commitments (M_m_n, M_1_n, neg_one) once, serially,
            // so the parallel pre-verify below only performs lookups.
            let mut fixed_comm_keys: std::collections::HashSet<(usize, usize)> =
                std::collections::HashSet::new();
            for e in a_verify.iter() {
                fixed_comm_keys.insert((e.m, e.n));
            }
            for e in b_verify.iter() {
                fixed_comm_keys.insert((1, e.n));
            }
            let verifier_fixed_comms: DashMap<(usize, usize), PrecomputedFixedComms> =
                DashMap::new();
            for (m, n) in fixed_comm_keys {
                let M_m_n: Vec<Vec<i64>> = vec![vec![big_M as i64; n]; m];
                let mat_M_m_n = vec_mat_to_zkmatrix_i64("M_m_n".to_string(), &M_m_n);
                let (M_m_n_comm, _) = mat_M_m_n.commit_rm(&zkp_pp.zk_matrix_srs);
                let M_1_n: Vec<Vec<i64>> = vec![vec![big_M as i64; n]];
                let mat_M_1_n = vec_mat_to_zkmatrix_i64("M_1_n".to_string(), &M_1_n);
                let (M_1_n_comm, _) = mat_M_1_n.commit_rm(&zkp_pp.zk_matrix_srs);
                let neg_one = vec![vec![-1i64]];
                let mat_neg_one = vec_mat_to_zkmatrix_i128("-1".to_string(), &neg_one);
                let (neg_one_comm, _) = mat_neg_one.commit_cm(&zkp_pp.zk_matrix_srs);
                verifier_fixed_comms.insert(
                    (m, n),
                    PrecomputedFixedComms { M_m_n_comm, M_1_n_comm, neg_one_comm },
                );
            }

            total_prove_time += prove_timer.elapsed().as_millis();

            if total_prove_time > timeout_ms {
                writeln!(
                    log_file,
                    "Sample {} - setup: {}ms, prove: {}ms, verify: {}ms - TIMED_OUT",
                    sample, total_setup_time, total_prove_time, total_verify_time
                ).unwrap();
                break;
            }

            // ========== PHASE 2.5b: Pre-verify unique A/b proofs ==========
            let preverify_timer = Instant::now();
            println!(
                "Phase 2.5b: Pre-verifying {} unique A/b proofs (par_iter)...",
                a_verify.len() + b_verify.len()
            );
            let verifier_A_cache: DashMap<HashableGtElement, ZKRPProof> = DashMap::new();
            let verifier_b_cache: DashMap<HashableGtElement, ZKRPProof> = DashMap::new();

            a_verify.par_iter().for_each(|entry| {
                let dims = ZkpDims { m: entry.m, n: entry.n, ..Default::default() };
                let fc = verifier_fixed_comms.get(&(entry.m, entry.n)).unwrap().clone();
                assert!(
                    verify_A_plus_M_zkrp(
                        &zkp_pp, &dims, entry.c_A, fc.M_m_n_comm,
                        &entry.A_plus_M_zkrp, big_M,
                    )
                );
                verifier_A_cache.insert(
                    HashableGtElement(entry.c_A),
                    entry.A_plus_M_zkrp.clone(),
                );
            });

            b_verify.par_iter().for_each(|entry| {
                let dims = ZkpDims { n: entry.n, ..Default::default() };
                let fc = verifier_fixed_comms.get(&(1, entry.n)).unwrap().clone();
                assert!(
                    verify_neg_b_plus_M_zkrp(
                        &zkp_pp, &dims, entry.c_b, fc.M_1_n_comm,
                        &entry.neg_b_plus_M_zkrp, big_M,
                    )
                );
                verifier_b_cache.insert(
                    HashableGtElement(entry.c_b),
                    entry.neg_b_plus_M_zkrp.clone(),
                );
            });

            total_verify_time += preverify_timer.elapsed().as_millis();

            if total_verify_time > timeout_ms {
                writeln!(
                    log_file,
                    "Sample {} - setup: {}ms, prove: {}ms, verify: {}ms - TIMED_OUT",
                    sample, total_setup_time, total_prove_time, total_verify_time
                ).unwrap();
                break;
            }

            // ========== PHASE 3: Chunk proofs + verification ==========
            println!("========== Chunk proofs + verification in one ==========");
            let mut timed_out = false;
            let mut all_successful = true;
            let total_chunks = input.obligations.len().div_ceil(chunk_size);

            for (chunk_idx, chunk) in input.obligations.chunks(chunk_size).enumerate() {
                if total_prove_time > timeout_ms {
                    timed_out = true;
                    break;
                }

                // --- prove ---
                let prove_timer = Instant::now();
                let proof_results: Vec<(ZkpProofCached, ZkpDims, Vec<Vec<i64>>, Vec<Vec<i64>>)> =
                    chunk
                        .par_iter()
                        .enumerate()
                        .map(|(_inner_idx, obligation)| {
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
                            let e3_scalar = obligation.computed_values.neg_h_p_T_mu_s;
                            let e3_padded = vec![vec![e3_scalar]];

                            let m = A_s_T.len();
                            let n = A_s_T[0].len();
                            let n_prime = G_p_T[0].len();
                            let max_n = if n >= n_prime { n } else { n_prime };
                            let q = max_n + 1;

                            let a = prover_A_cache.get(&(A_s_T.clone(), q)).unwrap();
                            let b = prover_b_cache.get(&(neg_b_s_T.clone(), q)).unwrap();

                            let A_lambda_e1_dims =
                                ZkMatMulDimsCached::new(&A_s_T, &lambda_s, &e1_padded);
                            let b_lambda_e2_dims =
                                ZkMatMulDimsCached::new(&neg_b_s_T, &lambda_s, &e2_padded);
                            let dims = ZkpDims {
                                m,
                                n,
                                n_prime,
                                A_lambda_e1_dims,
                                b_lambda_e2_dims,
                            };

                            // lambda: fresh ZKRP
                            let lambda_r = bls::ZpElement::rand();
                            let (lambda_comm, lambda_cache) =
                                vec_mat_to_zkmatrix_i64("lambda_s".to_string(), &lambda_s)
                                    .commit_cm(&zkp_pp.zk_matrix_srs);
                            let lambda_blind = lambda_comm
                                + (lambda_r * zkp_pp.zk_matrix_srs.blind_base);
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
                            let lambda_zkrp = zkrp::prove(
                                &lambda_zkrp_pp, &lambda_s, lambda_blind, lambda_r,
                                zkp_pp.big_M, alpha,
                            );

                            // mu: fresh ZKRP
                            let mu_r = bls::ZpElement::rand();
                            let (mu_comm, _) =
                                vec_mat_to_zkmatrix_i64("mu_s".to_string(), &mu_s)
                                    .commit_cm(&zkp_pp.zk_matrix_srs);
                            let mu_blind = mu_comm
                                + (mu_r * zkp_pp.zk_matrix_srs.blind_base);
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
                            let mu_zkrp = zkrp::prove(
                                &mu_zkrp_pp, &mu_s, mu_blind, mu_r,
                                zkp_pp.big_M, alpha,
                            );

                            // e1: A_s^T * lambda_s, fresh ZkMatMul
                            let mat_A = vec_mat_to_zkmatrix_i64("A_s^T".to_string(), &A_s_T);
                            let mat_lambda = vec_mat_to_zkmatrix_i64("lambda_s".to_string(), &lambda_s);
                            let mat_e1 = vec_mat_to_zkmatrix_i128("e_1".to_string(), &e1_padded);
                            let e1_r = a.A_r + lambda_r;
                            let (e1_comm, e1_cache) = mat_e1.commit_cm(&zkp_pp.zk_matrix_srs);
                            let e1_blind = e1_comm + (e1_r * zkp_pp.zk_matrix_srs.blind_base);
                            let e1_protocol = ZkMatMul::new(
                                e1_blind, a.A_blind, lambda_blind,
                                mat_e1.shape.0, mat_e1.shape.1, mat_A.shape.1,
                            );
                            let mut e1_prover = ZkTranSeqProver::new(&zkp_pp.zk_matrix_srs);
                            e1_protocol.prove::<i128, i64, i64>(
                                &zkp_pp.zk_matrix_srs, &mut e1_prover,
                                mat_e1, mat_A, mat_lambda.clone(),
                                &e1_cache, &a.A_cache, &lambda_cache,
                                e1_r, a.A_r, lambda_r,
                            );
                            let e1_zkmm = e1_prover.publish_trans();

                            // e2: -b_s^T * lambda_s, fresh ZkMatMul
                            let mat_b = vec_mat_to_zkmatrix_i64("-b_s^T".to_string(), &neg_b_s_T);
                            let mat_e2 = vec_mat_to_zkmatrix_i128("e_2".to_string(), &e2_padded);
                            let e2_r = b.b_r + lambda_r;
                            let (e2_comm, e2_cache) = mat_e2.commit_cm(&zkp_pp.zk_matrix_srs);
                            let e2_blind = e2_comm + (e2_r * zkp_pp.zk_matrix_srs.blind_base);
                            let e2_protocol = ZkMatMul::new(
                                e2_blind, b.b_blind, lambda_blind,
                                mat_e2.shape.0, mat_e2.shape.1, mat_b.shape.1,
                            );
                            let mut e2_prover = ZkTranSeqProver::new(&zkp_pp.zk_matrix_srs);
                            e2_protocol.prove::<i128, i64, i64>(
                                &zkp_pp.zk_matrix_srs, &mut e2_prover,
                                mat_e2, mat_b, mat_lambda,
                                &e2_cache, &b.b_cache, &lambda_cache,
                                e2_r, b.b_r, lambda_r,
                            );
                            let e2_zkmm = e2_prover.publish_trans();

                            // e3: -h_p^T * mu_s, fresh commitment
                            let h_r = bls::ZpElement::rand();
                            let mat_e3 = vec_mat_to_zkmatrix_i128("e_3".to_string(), &e3_padded);
                            let (e3_comm, _) = mat_e3.commit_cm(&zkp_pp.zk_matrix_srs);
                            let e3_blind = e3_comm
                                + ((h_r + mu_r) * zkp_pp.zk_matrix_srs.blind_base);

                            let pieces = CachedProverPieces {
                                A_r: a.A_r, A_blind: a.A_blind,
                                A_plus_M_zkrp: a.A_plus_M_zkrp.clone(),
                                b_r: b.b_r, b_blind: b.b_blind,
                                neg_b_plus_M_zkrp: b.neg_b_plus_M_zkrp.clone(),
                                mu_s: mu_s.clone(), mu_r, mu_blind, mu_zkrp,
                                lambda_r, lambda_blind, lambda_zkrp,
                                e1_r, e1_blind, e1_zkmm,
                                e2_scalar, e2_r, e2_blind, e2_zkmm,
                                e3_scalar, e3_blind, h_r,
                            };
                            let zkp_proof = prove_cached(
                                &zkp_pp, &dims, &G_p_T, &h_p_T, &pieces, alpha,
                            );

                            (zkp_proof, dims, G_p_T, h_p_T)
                        })
                        .collect::<Vec<_>>();
                total_prove_time += prove_timer.elapsed().as_millis();

                println!(
                    "Chunk {}/{} - proved {} obligations",
                    chunk_idx + 1, total_chunks, proof_results.len()
                );

                if total_prove_time > timeout_ms {
                    timed_out = true;
                    break;
                }

                // --- verify ---
                let verify_timer = Instant::now();
                let verify_results: Vec<bool> = proof_results
                    .par_iter()
                    .enumerate()
                    .map(|(_inner_idx, (zkp_proof, dims, G_p_T, h_p_T))| {
                        let mut zkp_verified = false;

                        let (is_a_cached, a_mismatch) = {
                            let key = HashableGtElement(zkp_proof.c_A);
                            match verifier_A_cache.get(&key) {
                                Some(cached) if *cached == zkp_proof.A_plus_M_zkrp_proof => (true, false),
                                Some(_) => (false, true),
                                None => (false, false),
                            }
                        };
                        let (is_b_cached, b_mismatch) = {
                            let key = HashableGtElement(zkp_proof.c_b);
                            match verifier_b_cache.get(&key) {
                                Some(cached) if *cached == zkp_proof.neg_b_plus_M_zkrp_proof => (true, false),
                                Some(_) => (false, true),
                                None => (false, false),
                            }
                        };

                        if a_mismatch || b_mismatch {
                            zkp_verified = false;
                        } else {
                            let flags = VerifierCacheFlagsCached {
                                is_A_plus_M_cached: is_a_cached,
                                is_neg_b_plus_M_cached: is_b_cached,
                                is_lambda_cached: false,
                                is_mu_cached: false,
                                is_e1_cached: false,
                                is_e2_cached: false,
                            };
                            let fc_val = verifier_fixed_comms
                                .get(&(dims.m, dims.n))
                                .map(|fc| fc.clone())
                                .unwrap_or_else(|| {
                                    let M_m_n: Vec<Vec<i64>> =
                                        vec![vec![big_M as i64; dims.n]; dims.m];
                                    let mat_M_m_n =
                                        vec_mat_to_zkmatrix_i64("M_m_n".to_string(), &M_m_n);
                                    let (M_m_n_comm, _) =
                                        mat_M_m_n.commit_rm(&zkp_pp.zk_matrix_srs);
                                    let M_1_n: Vec<Vec<i64>> =
                                        vec![vec![big_M as i64; dims.n]];
                                    let mat_M_1_n =
                                        vec_mat_to_zkmatrix_i64("M_1_n".to_string(), &M_1_n);
                                    let (M_1_n_comm, _) =
                                        mat_M_1_n.commit_rm(&zkp_pp.zk_matrix_srs);
                                    let neg_one = vec![vec![-1i64]];
                                    let mat_neg_one =
                                        vec_mat_to_zkmatrix_i128("-1".to_string(), &neg_one);
                                    let (neg_one_comm, _) =
                                        mat_neg_one.commit_cm(&zkp_pp.zk_matrix_srs);
                                    let comms = PrecomputedFixedComms {
                                        M_m_n_comm,
                                        M_1_n_comm,
                                        neg_one_comm,
                                    };
                                    verifier_fixed_comms
                                        .insert((dims.m, dims.n), comms.clone());
                                    comms
                                });
                            zkp_verified = zkp_proof.verify(
                                &zkp_pp, &dims, G_p_T, h_p_T, &flags, Some(&fc_val),
                            );
                            if zkp_verified {
                                if !is_a_cached {
                                    verifier_A_cache.insert(
                                        HashableGtElement(zkp_proof.c_A),
                                        zkp_proof.A_plus_M_zkrp_proof.clone(),
                                    );
                                }
                                if !is_b_cached {
                                    verifier_b_cache.insert(
                                        HashableGtElement(zkp_proof.c_b),
                                        zkp_proof.neg_b_plus_M_zkrp_proof.clone(),
                                    );
                                }
                            }
                        }

                        zkp_verified
                    })
                    .collect::<Vec<_>>();
                total_verify_time += verify_timer.elapsed().as_millis();

                if total_verify_time > timeout_ms {
                    timed_out = true;
                    break;
                }

                if !verify_results.iter().all(|&v| v) {
                    all_successful = false;
                }

                println!(
                    "Chunk {}/{} - verified {} obligations ({}/{} passed)",
                    chunk_idx + 1,
                    total_chunks,
                    verify_results.len(),
                    verify_results.iter().filter(|&&v| v).count(),
                    verify_results.len()
                );
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

    let g_hat_mat: Vec<Vec<bls::GtElement>> = (0..q)
        .into_par_iter()
        .map(|j| (0..q).map(|k| g_hat_j[j] * g_hat_i[k]).collect())
        .collect();

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

criterion_group!(benches, prove_and_verify_benchmarks_full_cache);
criterion_main!(benches);
