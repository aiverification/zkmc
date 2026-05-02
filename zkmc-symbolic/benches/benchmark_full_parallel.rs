use bls_bulletproofs::group::ff::Field;
use criterion::{Criterion, criterion_group, criterion_main};
use rand::SeedableRng;
use rand_chacha::ChaChaRng;
use rayon::prelude::*;
use serde::Deserialize;
use serde_json::Value;
use std::{collections::HashMap, fs, fs::File, io::Write, path::Path, time::Instant};
use zkmatrix::commit_mat::CommitMat;
use zkmatrix::utils::curve as bls;
use zkmc_symbolic::zkrp;
use zkmc_symbolic::{utils::*, zkp, zkp::*, zkrp::*};

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
struct prover_A_s_T_cache_entry {
    A_s_T: Vec<Vec<i64>>,
    A_plus_M_proof: Option<zkrp::ZKRPProof>,
    A_r: Option<bls::ZpElement>,
    A_comm: Option<bls::GtElement>,
    A_cache: Option<Vec<bls::G2Element>>,
    A_blind: Option<bls::GtElement>,
    q: usize,
}

#[derive(Debug, Clone)]
struct prover_neg_b_s_T_cache_entry {
    neg_b_s_T: Vec<Vec<i64>>,
    neg_b_plus_M_proof: Option<zkrp::ZKRPProof>,
    b_r: Option<bls::ZpElement>,
    b_comm: Option<bls::GtElement>,
    b_cache: Option<Vec<bls::G2Element>>,
    b_blind: Option<bls::GtElement>,
    q: usize,
}

#[derive(Debug, Clone)]
struct verifier_A_s_T_cache_entry {
    q: usize,
    comm_A: bls::GtElement,
    A_plus_M_proof: ZKRPProof,
}

#[derive(Debug, Clone)]
struct verifier_neg_b_s_T_cache_entry {
    q: usize,
    comm_b: bls::GtElement,
    neg_b_plus_M_proof: ZKRPProof,
}

fn prove_and_verify_benchmarks(c: &mut Criterion) {
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
            let all_successful;
            let total_setup_time: u128;
            let mut total_prove_time: u128 = 0;
            let mut total_verify_time: u128 = 0;

            // One time fake setup
            let prev_zkp_pp: ZkpSRS;
            let q = 2usize;
            let pc_gens = PedersenGens::default();
            let bp_gens = BulletproofGens::new(64, (q * 2) as usize);
            let g_blstrs: blstrs::G1Affine = pc_gens.B.into();
            let g_bls = blstrs_affine_to_bls_g1(&g_blstrs);

            let (throwaway_srs, alpha) =
                zkmatrix::setup::SRS::new_with_chosen_g_return_s_hat(32, g_bls); //Used for g_prime
            let g_prime = throwaway_srs.h_hat.clone();

            let A_lambda_e1_dims =
                ZkMatMulDims::new(&vec![vec![2]], &vec![vec![2]], &vec![vec![2]]);
            let b_lambda_e2_dims =
                ZkMatMulDims::new(&vec![vec![2]], &vec![vec![2]], &vec![vec![2]]);

            prev_zkp_pp = construct_zkp_srs(
                2,
                1usize,
                1usize,
                1usize,
                g_blstrs,
                g_bls,
                g_prime,
                alpha,
                A_lambda_e1_dims,
                b_lambda_e2_dims,
                pc_gens,
                bp_gens,
                2u32.pow(31) - 1,
            );

            let mut prover_A_cache: HashMap<MatrixCacheKey, prover_A_s_T_cache_entry> =
                HashMap::new();
            let mut prover_b_cache: HashMap<MatrixCacheKey, prover_neg_b_s_T_cache_entry> =
                HashMap::new();
            // TODO - add verifier caching
            let mut verifier_A_cache: HashMap<(usize, bls::GtElement), verifier_A_s_T_cache_entry> =
                HashMap::new();
            let mut verifier_b_cache: HashMap<
                (usize, bls::GtElement),
                verifier_neg_b_s_T_cache_entry,
            > = HashMap::new();
            let big_M = 2u32.pow(31) - 1;

            let mut max_q: usize = 0;
            for obligation in input.obligations.iter() {
                let pad_A = pad_matrix(&obligation.matrices.A_s);
                let pad_G = pad_matrix(&obligation.matrices.G_p);
                if pad_A.len() > max_q {
                    max_q = pad_A.len();
                } else if pad_A[0].len() > max_q {
                    max_q = pad_A[0].len();
                } else if pad_G.len() > max_q {
                    max_q = pad_G.len();
                } else if pad_G[0].len() > max_q {
                    max_q = pad_G[0].len();
                }
            }

            // ========== PHASE 1: SRS Setup Only ==========
            println!("================ Phase 1: SRS Setup ================");
            let setup_timer = Instant::now();

            // Collect unique matrices for dimension calculation
            let mut unique_A_matrices: HashMap<MatrixCacheKey, Vec<Vec<i64>>> = HashMap::new();
            let mut unique_b_matrices: HashMap<MatrixCacheKey, Vec<Vec<i64>>> = HashMap::new();

            for obligation in input.obligations.iter() {
                let A_s_T = pad_matrix(&transpose_matrix(&obligation.matrices.A_s));
                let neg_b_s_T =
                    pad_matrix(&transpose_matrix(&negate_matrix(&obligation.matrices.b_s)));
                let G_p_T = pad_matrix(&transpose_matrix(&obligation.matrices.G_p));

                // let m = A_s_T.len();
                let n = A_s_T[0].len();
                let n_prime = G_p_T[0].len();
                let max_n = if n >= n_prime { n } else { n_prime };
                let q = max_n + 1;

                unique_A_matrices.entry((A_s_T.clone(), q)).or_insert(A_s_T);
                unique_b_matrices
                    .entry((neg_b_s_T.clone(), q))
                    .or_insert(neg_b_s_T);
            }

            println!(
                "Unique A matrices: {}, Unique b matrices: {}",
                unique_A_matrices.len(),
                unique_b_matrices.len()
            );

            // Create SRS large enough for max_q
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
                zkmatrix::setup::SRS::new_with_chosen_g_return_s_hat(32, g_bls); //Used for g_prime
            let g_prime = throwaway_srs.h_hat.clone();

            let A_lambda_e1_dims = ZkMatMulDims::new(&first_A_s_T, &first_lambda_s, &first_e_1);
            let b_lambda_e2_dims = ZkMatMulDims::new(&first_neg_b_s_T, &first_lambda_s, &first_e_2);

            let zkp_pp = construct_zkp_srs(
                max_q,
                1usize,
                1usize,
                1usize,
                g_blstrs,
                g_bls,
                g_prime,
                alpha,
                A_lambda_e1_dims,
                b_lambda_e2_dims,
                pc_gens,
                bp_gens,
                big_M,
            );

            total_setup_time = setup_timer.elapsed().as_millis();
            println!("Phase 1 complete. Setup time: {}ms", total_setup_time);

            // ========== PHASE 2: Prover (Cache Population + Proof Generation) ==========
            println!("================ Phase 2: Prover ================");
            let prove_timer = Instant::now();

            // Pre-compute all unique A matrix commitments and ZKRP proofs in parallel
            println!(
                "Pre-computing {} unique A matrix proofs (parallel)...",
                unique_A_matrices.len()
            );
            let a_results: Vec<_> = unique_A_matrices
                .par_iter()
                .map(|((A_s_T, q), _)| {
                    let temp_A_r = bls::ZpElement::rand();
                    let (temp_A_comm, temp_A_cache) =
                        vec_mat_to_zkmatrix_i64("A_s^T".to_string(), &A_s_T)
                            .commit_rm(&zkp_pp.zk_matrix_srs);
                    let temp_A_blind = temp_A_comm + (temp_A_r * zkp_pp.zk_matrix_srs.blind_base);

                    // Compute A+M for ZKRP
                    let mat_m = A_s_T.len();
                    let mat_n = A_s_T[0].len();
                    let mut A_plus_M: Vec<Vec<i64>> = vec![vec![zkp_pp.big_M as i64; mat_n]; mat_m];
                    let mut A_plus_M_g_hat_i: Vec<bls::GtElement> =
                        Vec::with_capacity(mat_n * mat_m);
                    for j in 0..mat_m {
                        for k in 0..mat_n {
                            A_plus_M[j][k] += A_s_T[j][k];
                            A_plus_M_g_hat_i.push(zkp_pp.g_hat_mat[j][k].clone());
                        }
                    }
                    println!("Computed A+M");

                    let A_plus_M_l = mat_m * mat_n;
                    let mut A_plus_M_g_i = zkp_pp.g_i_vec[0..2 * A_plus_M_l].to_vec();
                    A_plus_M_g_i[A_plus_M_l] = get_bls_g1_zero();

                    // Compute M commitment for A_prime_comm
                    let M_m_n: Vec<Vec<i64>> = vec![vec![zkp_pp.big_M as i64; mat_n]; mat_m];
                    let mat_M_m_n = vec_mat_to_zkmatrix_i64("M_m_n".to_string(), &M_m_n);
                    let (M_m_n_comm, _) = mat_M_m_n.commit_rm(&zkp_pp.zk_matrix_srs);
                    let A_prime_comm = temp_A_blind + M_m_n_comm;

                    let A_plus_M_zkrp_pp = zkrp::ZKRPParams {
                        l: A_plus_M_l,
                        m: mat_m,
                        n: mat_n,
                        g_blstrs: zkp_pp.g_blstrs,
                        g_bls: zkp_pp.g_bls,
                        g_prime: zkp_pp.g_prime,
                        g_prime_alpha: zkp_pp.g_prime_alpha,
                        g_i: zkp_pp.g_i_vec.clone(),
                        h: zkp_pp.h,
                        h_prime: zkp_pp.h_prime,
                        zk_matrix_srs: zkp_pp.zk_matrix_srs.clone(),
                        pc_gens: zkp_pp.pc_gens.clone(),
                        bp_gens: zkp_pp.bp_gens.clone(),
                    };

                    let A_plus_M_proof = zkrp::prove(
                        &A_plus_M_zkrp_pp,
                        &A_plus_M,
                        A_prime_comm,
                        temp_A_r,
                        2 * zkp_pp.big_M,
                        alpha,
                    );

                    (
                        (A_s_T.clone(), *q),
                        prover_A_s_T_cache_entry {
                            A_s_T: A_s_T.clone(),
                            A_plus_M_proof: Some(A_plus_M_proof),
                            A_r: Some(temp_A_r),
                            A_comm: Some(temp_A_comm),
                            A_cache: Some(temp_A_cache),
                            A_blind: Some(temp_A_blind),
                            q: *q,
                        },
                    )
                })
                .collect();

            // Insert A results into cache
            for (key, entry) in a_results {
                prover_A_cache.insert(key, entry);
            }

            // Pre-compute all unique b proofs in parallel
            println!(
                "Pre-computing {} unique b matrix proofs (parallel)...",
                unique_b_matrices.len()
            );
            let b_results: Vec<_> = unique_b_matrices
                .par_iter()
                .map(|((neg_b_s_T, q), _)| {
                    let temp_b_r = bls::ZpElement::rand();
                    let (temp_b_comm, temp_b_cache) =
                        vec_mat_to_zkmatrix_i64("-b_s^T".to_string(), &neg_b_s_T)
                            .commit_rm(&zkp_pp.zk_matrix_srs);
                    let temp_b_blind = temp_b_comm + (temp_b_r * zkp_pp.zk_matrix_srs.blind_base);

                    // Compute neg_b+M for ZKRP (b is 1xn)
                    let mat_n = neg_b_s_T[0].len();
                    let mut neg_b_plus_M: Vec<Vec<i64>> = vec![vec![zkp_pp.big_M as i64; mat_n]];
                    let mut neg_b_plus_M_g_hat_i: Vec<bls::GtElement> = Vec::with_capacity(mat_n);
                    for k in 0..mat_n {
                        neg_b_plus_M[0][k] += neg_b_s_T[0][k];
                        neg_b_plus_M_g_hat_i.push(zkp_pp.g_hat_mat[0][k].clone());
                    }

                    let neg_b_plus_M_l = mat_n;
                    let mut neg_b_plus_M_g_i = zkp_pp.g_i_vec[0..2 * neg_b_plus_M_l].to_vec();
                    neg_b_plus_M_g_i[neg_b_plus_M_l] = get_bls_g1_zero();

                    // Compute M commitment for b_prime_comm
                    let M_1_n: Vec<Vec<i64>> = vec![vec![zkp_pp.big_M as i64; mat_n]];
                    let mat_M_1_n = vec_mat_to_zkmatrix_i64("M_1_n".to_string(), &M_1_n);
                    let (M_1_n_comm, _) = mat_M_1_n.commit_rm(&zkp_pp.zk_matrix_srs);
                    let b_prime_comm = temp_b_blind + M_1_n_comm;

                    let neg_b_plus_M_zkrp_pp = zkrp::ZKRPParams {
                        l: neg_b_plus_M_l,
                        m: 1,
                        n: mat_n,
                        g_blstrs: zkp_pp.g_blstrs,
                        g_bls: zkp_pp.g_bls,
                        g_prime: zkp_pp.g_prime,
                        g_prime_alpha: zkp_pp.g_prime_alpha,
                        g_i: zkp_pp.g_i_vec.clone(),
                        h: zkp_pp.h,
                        h_prime: zkp_pp.h_prime,
                        zk_matrix_srs: zkp_pp.zk_matrix_srs.clone(),
                        pc_gens: zkp_pp.pc_gens.clone(),
                        bp_gens: zkp_pp.bp_gens.clone(),
                    };

                    let neg_b_plus_M_proof = zkrp::prove(
                        &neg_b_plus_M_zkrp_pp,
                        &neg_b_plus_M,
                        b_prime_comm,
                        temp_b_r,
                        2 * zkp_pp.big_M,
                        alpha,
                    );

                    (
                        (neg_b_s_T.clone(), *q),
                        prover_neg_b_s_T_cache_entry {
                            neg_b_s_T: neg_b_s_T.clone(),
                            neg_b_plus_M_proof: Some(neg_b_plus_M_proof),
                            b_r: Some(temp_b_r),
                            b_comm: Some(temp_b_comm),
                            b_cache: Some(temp_b_cache),
                            b_blind: Some(temp_b_blind),
                            q: *q,
                        },
                    )
                })
                .collect();

            // Insert b results into cache
            for (key, entry) in b_results {
                prover_b_cache.insert(key, entry);
            }

            total_prove_time += prove_timer.elapsed().as_millis();

            println!("========== Chunk proofs + verification in one ==========");
            let verification_results: Vec<bool> = input
                .obligations
                .chunks(chunk_size)
                .enumerate()
                .flat_map(|(chunk_idx, chunk)| {
                    let prove_timer = Instant::now();
                    let proof_results: Vec<(zkp::ZkpProof, ZkpSRS, Vec<Vec<i64>>, Vec<Vec<i64>>)> =
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
                                let e_1 = pad_matrix(&obligation.computed_values.A_s_T_lambda_s);
                                let e_2 = vec![vec![obligation.computed_values.neg_b_s_T_lambda_s]];
                                let e_3 = vec![vec![obligation.computed_values.neg_h_p_T_mu_s]];

                                let m = A_s_T.len();
                                let n = A_s_T[0].len();
                                let n_prime = G_p_T[0].len();
                                let max_n = if n >= n_prime { n } else { n_prime };
                                let q = max_n + 1;

                                // O(1) cache lookups
                                let A_cached = prover_A_cache.get(&(A_s_T.clone(), q)).unwrap();
                                let b_cached = prover_b_cache.get(&(neg_b_s_T.clone(), q)).unwrap();

                                // Clone and update SRS dimensions for this obligation
                                let A_lambda_e1_dims = ZkMatMulDims::new(&A_s_T, &lambda_s, &e_1);
                                let b_lambda_e2_dims =
                                    ZkMatMulDims::new(&neg_b_s_T, &lambda_s, &e_2);
                                let mut local_zkp_pp = zkp_pp.clone();
                                local_zkp_pp.A_lambda_e1_dims = A_lambda_e1_dims;
                                local_zkp_pp.b_lambda_e2_dims = b_lambda_e2_dims;
                                local_zkp_pp.m = m;
                                local_zkp_pp.n = n;
                                local_zkp_pp.n_prime = n_prime;

                                // Generate proof using cached values
                                let zkp_proof = zkp::prove(
                                    &local_zkp_pp,
                                    &A_s_T,
                                    &neg_b_s_T,
                                    &lambda_s,
                                    &mu_s,
                                    &G_p_T,
                                    &h_p_T,
                                    &e_1,
                                    &e_2,
                                    &e_3,
                                    A_cached.A_comm,
                                    A_cached.A_cache.clone(),
                                    A_cached.A_r,
                                    A_cached.A_blind,
                                    b_cached.b_comm,
                                    b_cached.b_cache.clone(),
                                    b_cached.b_r,
                                    b_cached.b_blind,
                                    A_cached.A_plus_M_proof.clone(),
                                    b_cached.neg_b_plus_M_proof.clone(),
                                    alpha,
                                );

                                println!("Obligation {} proof generated", idx + 1);

                                // Return proof + context needed for verification
                                (zkp_proof, local_zkp_pp, G_p_T, h_p_T)
                            })
                            .collect::<Vec<_>>();
                    total_prove_time += prove_timer.elapsed().as_millis();
                    let verify_timer = Instant::now();
                    let verify_results = proof_results
                        .par_iter()
                        .enumerate()
                        .map(|(inner_idx, (zkp_proof, local_zkp_pp, G_p_T, h_p_T))| {
                            let idx = chunk_idx * chunk_size + inner_idx;
                            let zkp_verified =
                                zkp_proof.verify(local_zkp_pp, G_p_T, h_p_T, true, true);
                            println!("Obligation {} verified: {}", idx + 1, zkp_verified);
                            zkp_verified
                        })
                        .collect::<Vec<_>>();
                    total_verify_time += verify_timer.elapsed().as_millis();
                    println!("====== Chunk {} done ======", chunk_idx + 1);
                    verify_results
                })
                .collect::<Vec<_>>();

            println!("Phase 2 complete. Prove time: {}ms", total_prove_time);
            println!("Phase 3 complete. Verify time: {}ms", total_verify_time);

            // Check results
            all_successful = verification_results.iter().all(|&verified| verified);

            println!("All verified: {}", all_successful);

            if all_successful {
                writeln!(log_file, "Sample number: {:?} -- setup time (ms): {:?} -- prove time (ms): {:?} -- verify time (ms): {:?}", sample, total_setup_time, total_prove_time, total_verify_time).unwrap();
                break;
            } else {
                writeln!(
                    log_file,
                    "Sample number: {:?} -- Did not all verify correctly.",
                    sample
                )
                .unwrap();
            }
        }
    }
}

pub fn construct_zkp_srs(
    q: usize,
    m: usize,
    n: usize,
    n_prime: usize,
    g_blstrs: blstrs::G1Affine,
    g_bls: bls::G1Element,
    g_prime: bls::G2Element,
    alpha: bls::ZpElement,
    A_lambda_e1_dims: ZkMatMulDims,
    b_lambda_e2_dims: ZkMatMulDims,
    mut pc_gens: PedersenGens,
    bp_gens: BulletproofGens,
    big_M: u32,
) -> ZkpSRS {
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

    return ZkpSRS {
        m,
        n,
        n_prime,
        l,
        q,
        big_M,
        g_blstrs,
        g_bls,
        g_prime,
        g_prime_alpha,
        g_i_vec,
        h,
        h_prime,
        g_hat_mat: g_hat_mat.clone(),
        pc_gens: pc_gens.clone(),
        bp_gens,
        zk_matrix_srs,
        A_lambda_e1_dims,
        b_lambda_e2_dims,
    };
}

criterion_group!(benches, prove_and_verify_benchmarks);
criterion_main!(benches);
