use bls_bulletproofs::{BulletproofGens, PedersenGens};
use rayon::prelude::*;
use serde::Deserialize;
use std::fs;
use criterion::{Criterion, criterion_group, criterion_main};
use std::time::Instant;
use std::collections::HashMap;
use std::io::Write;
use blstrs;

use zkmc_symbolic_folding::utils::plain_utils::*;
use zkmc_symbolic_folding::utils::curve_utils::*;
use zkmc_symbolic_folding::utils::public_exponent_schedule::*;
use zkmc_symbolic_folding::zkrp;
use zkmc_symbolic_folding::zkp;

const INPUT_DIR: &str = "input/";
const OUTPUT_DIR: &str = "outputs/";
const NUM_LOOPS: usize = 1;

// Files to benchmark (read from INPUT_DIR); each gets OUTPUT_DIR/bench_<name>.log.
const CANDIDATE_FILES: [&str; 3] = [
    "exb_i4a2.json",
    "rr_2.json",
    "dhcp_noOFF_7_2_7.json",
];

#[derive(Debug, Deserialize)]
struct FullZkpData {
    obligations: Vec<Obligation>,
    count: usize,
    constants: HashMap<String, i64>,
}

#[derive(Debug, Deserialize)]
struct Obligation {
    obligation_type: String,
    matrices: ObligationMatrices,
    dimensions: Dimensions,

    program_transition: Option<usize>,
    automaton_transition: Option<AutomatonTransition>,
    source_ranking_state: Option<String>,
    target_ranking_state: Option<String>,
    source_case_idx: Option<usize>,
    target_case_idx: Option<usize>,
    infinity_case_idx: Option<usize>,
    is_fair: Option<bool>,

    witness: Witness,
    computed_values: ComputedValues,
    satisfiable: bool,
}

#[allow(non_snake_case)]
#[derive(Debug, Deserialize)]
struct ObligationMatrices {
    A_s: Vec<Vec<i64>>,
    b_s: Vec<Vec<i64>>,
    G_p: Vec<Vec<i64>>,
    h_p: Vec<Vec<i64>>,
}

#[derive(Debug, Deserialize)]
struct Dimensions {
    n_vars: usize,
    n_lambda_s: usize,
    n_mu_s: usize,
}

#[derive(Debug, Deserialize)]
struct AutomatonTransition {
    from: String,
    to: String,
}

#[allow(non_snake_case)]
#[derive(Debug, Deserialize)]
struct Witness {
    lambda_s: Vec<Vec<i64>>,
    mu_s: Vec<Vec<i64>>,
}

#[allow(non_snake_case)]
#[derive(Debug, Deserialize)]
struct ComputedValues {
    neg_b_s_T_lambda_s: i64,
    neg_h_p_T_mu_s: i64,
    A_s_T_lambda_s: Vec<Vec<i64>>,
    G_p_T_mu_s: Vec<Vec<i64>>,
}

fn full_prove_and_verify(_c: &mut Criterion) {
    fs::create_dir_all(OUTPUT_DIR).unwrap();

    for input_file in CANDIDATE_FILES {
        let log_path = format!("{OUTPUT_DIR}bench_{input_file}.log");
        let mut log_file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .unwrap();

        for loop_idx in 0..NUM_LOOPS {
            let file_path = format!("{INPUT_DIR}{input_file}");
            let read_timer = Instant::now();
            let json = fs::read_to_string(&file_path)
                .expect("failed to read full ZKP test data");
            let data: FullZkpData = serde_json::from_str(&json)
                .expect("failed to parse full ZKP test data");

            assert_eq!(data.count, data.obligations.len());
            let obligations = data.obligations;
            let read_time = read_timer.elapsed().as_millis();
            let N = obligations.len();
            let schedule = salem_spencer_schedule(N);

            println!(
                "Loaded {} obligations from {}",
                obligations.len(),
                file_path
            );

        // Now pad + transpose matrices
        // A^T is mxn, lambda is nx1, b^T is 1xn, G^T is mxn', mu is n'x1, h^T is 1xn' - consider for max_n we need max(n, n')
        let max_calc_timer = Instant::now();
        let mut max_m = obligations[0].matrices.A_s.len();
        let mut max_n = obligations[0].matrices.A_s[0].len();
        for obl in obligations.iter(){
            // Since we find max_m, max_n before transposing, they're flipped here
            max_m = max_m.max(obl.matrices.A_s[0].len());
            max_m = max_m.max(obl.matrices.G_p[0].len());
            max_n = max_n.max(obl.matrices.A_s.len());
            max_n = max_n.max(obl.matrices.G_p.len());
        }
        max_m = max_m.next_power_of_two();
        max_n = max_n.next_power_of_two();
        let max_calc_time = max_calc_timer.elapsed().as_millis();
        println!("Calculated max m, n: {}, {}", max_m, max_n);

        let matrix_prep_timer = Instant::now();
        let mut A_i: Vec<Vec<Vec<i64>>> = obligations.par_iter().map(|obl| obl.matrices.A_s.clone()).collect();
        let mut neg_b_i: Vec<Vec<Vec<i64>>> = obligations.par_iter().map(|obl| negate_matrix(&obl.matrices.b_s)).collect();
        let mut lambda_i: Vec<Vec<Vec<i64>>> = obligations.par_iter().map(|obl| obl.witness.lambda_s.clone()).collect();
        let mut neg_G_i: Vec<Vec<Vec<i64>>> = obligations.par_iter().map(|obl| negate_matrix(&obl.matrices.G_p)).collect();
        let mut neg_h_i: Vec<Vec<Vec<i64>>> = obligations.par_iter().map(|obl| negate_matrix(&obl.matrices.h_p)).collect();
        let mut mu_i: Vec<Vec<Vec<i64>>> = obligations.par_iter().map(|obl| obl.witness.mu_s.clone()).collect();
        let mut e_i: Vec<Vec<Vec<i64>>> = obligations.par_iter().map(|obl| obl.computed_values.A_s_T_lambda_s.clone()).collect();

        A_i.par_iter_mut().for_each(|mut A| transpose_matrix_in_place(&mut A));
        neg_b_i.par_iter_mut().for_each(|mut neg_b| transpose_matrix_in_place(&mut neg_b));
        neg_G_i.par_iter_mut().for_each(|mut neg_G| transpose_matrix_in_place(&mut neg_G));
        neg_h_i.par_iter_mut().for_each(|mut neg_h| transpose_matrix_in_place(&mut neg_h));

        pad_matrices_par_to_size(&mut A_i, max_m, max_n);
        pad_matrices_par_to_size(&mut neg_b_i, 1, max_n);
        pad_matrices_par_to_size(&mut lambda_i, max_n, 1);
        pad_matrices_par_to_size(&mut neg_G_i, max_m, max_n);
        pad_matrices_par_to_size(&mut neg_h_i, 1, max_n);
        pad_matrices_par_to_size(&mut mu_i, max_n, 1);
        pad_matrices_par_to_size(&mut e_i, max_m, 1);
        let matrix_prep_time = matrix_prep_timer.elapsed().as_millis();
        println!("Prepared matrices");

        // Sanity-check that A_s^T . lambda_s == e_A and G_p^T . mu_s == e_G.
        for i in 0..obligations.len(){
                let mut e_A = obligations[i].computed_values.A_s_T_lambda_s.clone();
                e_A = pad_matrix_to_size(&e_A, max_m, 1);
                let product_A = multiply_matrices_naive_par(&A_i[i], &lambda_i[i]);
                if product_A != e_A{
                    println!("A_s^T . lambda_s != e_A for i = {i}");
                    println!("A_s^T: {:?}", A_i[i]);
                    println!("lambda: {:?}", lambda_i[i]);
                    println!("e_A: {:?}", e_A);
                    println!("calculated e_A: {:?}", product_A);
                }

                let mut e_G = obligations[i].computed_values.G_p_T_mu_s.clone();
                e_G = pad_matrix_to_size(&e_G, max_m, 1);
                let neg_e_G = negate_matrix(&e_G);
                let product_G = multiply_matrices_naive_par(&neg_G_i[i], &mu_i[i]);
                if product_G != neg_e_G{
                    println!("G_p^T . mu_s != e_G for i = {i}");
                    println!("G_p^T: {:?}", neg_G_i[i]);
                    println!("mu: {:?}", mu_i[i]);
                    println!("neg_e_G: {:?}", neg_e_G);
                    println!("calculated e_G: {:?}", product_G);
                }

                if e_A != neg_e_G{
                    println!("e_A != -e_G for i = {i}");
                    println!("e_A: {:?}", e_A);
                    println!("-e_G: {:?}", e_G);
                }
        }

        // Now we calculate alpha_i, beta_i
        let alpha_beta_calc_timer = Instant::now();
        let mut alpha_i: Vec<Vec<Vec<i64>>> = vec![vec![vec![]]; A_i.len()];
        let mut beta_i: Vec<Vec<Vec<i64>>> = vec![vec![vec![]]; A_i.len()];
        alpha_i.par_iter_mut()
        .zip(neg_b_i.par_iter())
        .zip(lambda_i.par_iter())
        .for_each(|((alpha, neg_b), lambda)| {
            *alpha = multiply_matrices_naive_par(&neg_b, &lambda);
        });

        beta_i.par_iter_mut()
        .zip(neg_h_i.par_iter())
        .zip(mu_i.par_iter())
        .for_each(|((beta, neg_h), mu)| {
            *beta = multiply_matrices_naive_par(&neg_h, &mu);
        });
        let alpha_beta_calc_time = alpha_beta_calc_timer.elapsed().as_millis();
        println!("Calculated alpha, beta matrices");

        // Setup: public ZKP parameters (SRS, Pedersen/Bulletproof gens, ZKRP params)
        let setup_timer = Instant::now();
        let mut pc_gens = PedersenGens::default();
        let g_blstrs: blstrs::G1Affine = pc_gens.B.into();
        let g_bls = blstrs_affine_to_bls_g1(&g_blstrs);
        let aggregation_size = N.next_power_of_two();
        println!("RP aggregation size: {aggregation_size}");
        // Shared 32-bit BulletproofGens for all five range-proof types (v_i, lambda, mu, A, -b).
        // Party capacity must cover the largest single proof: A_i has max_m*max_n values.
        let bp_gens = BulletproofGens::new(32, (max_m * max_n).max(aggregation_size));
        let q = max_m.max(max_n) + 1;
        let (srs, s_hat) = zkmatrix::setup::SRS::new_with_chosen_g_return_s_hat(q, g_bls);
        let blind_factor_zp = s_hat.pow((q * q) as u64);
        let blind_factor_blstrs = bls_field_elem_to_blstrs_scalar(&blind_factor_zp);
        pc_gens.B_blinding = pc_gens.B * blind_factor_blstrs;

        // ZKRP params shared by lambda and mu range proofs (both padded to max_n x 1)
        let g_prime = srs.h_hat;
        let g_prime_alpha = g_prime * s_hat;
        let h = blstrs_proj_to_bls_g1(&pc_gens.B_blinding);
        let h_prime = g_prime * blind_factor_zp;
        assert!(max_n < N); // So that bp_gens works
        let lambda_mu_zkrp_pp = zkrp::ZKRPParams {
            l: max_n,
            m: max_n,
            n: 1,
            g_blstrs: g_blstrs,
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

        // ZKRP params for per-obligation A_i range proofs (max_m x max_n matrix)
        let A_zkrp_pp = zkrp::ZKRPParams {
            l: max_m * max_n,
            m: max_m,
            n: max_n,
            g_blstrs: g_blstrs,
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

        // ZKRP params for per-obligation -b_i range proofs (1 x max_n row)
        let b_zkrp_pp = zkrp::ZKRPParams {
            l: max_n,
            m: 1,
            n: max_n,
            g_blstrs: g_blstrs,
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

        let (pairs, committer) = zkp::ZKPParams::derive(&srs, &schedule, max_m, max_n);
        let params = zkp::ZKPParams {
            srs: srs.clone(),
            schedule,
            pairs,
            committer,
            pc_gens: pc_gens.clone(),
            bp_gens: bp_gens.clone(),
            big_M: 2u32.pow(31) - 1,
            max_m,
            max_n,
            N,
            aggregation_size,
            lambda_mu_zkrp_pp,
            A_zkrp_pp,
            b_zkrp_pp,
        };
        let setup_time = setup_timer.elapsed().as_millis();
        println!("Done SRS setup");
        let total_setup_time = read_time + max_calc_time + matrix_prep_time + alpha_beta_calc_time + setup_time;

        let input = zkp::ZKPInput {
            A_i,
            neg_b_i,
            lambda_i,
            neg_G_i,
            neg_h_i,
            mu_i,
            e_i,
            alpha_i,
            beta_i,
        };
        let statement = zkp::ZKPStatement {
            neg_G_i: input.neg_G_i.clone(),
            neg_h_i: input.neg_h_i.clone(),
        };

        // Prover + Verifier (full ZKP)
        let (proof, prover_total) = zkp::prove(&params, s_hat, &input);
        let (ok, verifier_total) = proof.verify(&params, &statement);
        println!("Loop {loop_idx} of {input_file}: ZKP verified: {ok}");

        let total_time = total_setup_time + prover_total + verifier_total;
        println!("");
        zkp::print_timing("Total time:", total_time);
        zkp::print_timing("--Total setup time:", total_setup_time);
        zkp::print_timing("----Read time:", read_time);
        zkp::print_timing("----Max m,n calc time:", max_calc_time);
        zkp::print_timing("----Matrix prep time:", matrix_prep_time);
        zkp::print_timing("----alpha, beta calc time:", alpha_beta_calc_time);
        zkp::print_timing("----SRS setup time:", setup_time);
        println!("");

        writeln!(
            log_file,
            "Loop {loop_idx} - setup: {total_setup_time}ms, prove: {prover_total}ms, verify: {verifier_total}ms, total: {total_time}ms - {}",
            if ok { "SUCCESS" } else { "VERIFICATION_FAILED" }
        ).unwrap();
        }
    }
}

criterion_group!(benches, full_prove_and_verify);
criterion_main!(benches);