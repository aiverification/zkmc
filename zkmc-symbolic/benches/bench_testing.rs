/*
    Benchmark used in testing. Reads input file, returns some info
    about the structure of the ZKP inputs, such as the number of unique
    A, -b, lambda, mu, e_1 (= alpha), e_2 (= beta), etc.
*/

use criterion::{Criterion, criterion_group, criterion_main};
use serde::Deserialize;
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::fs::File;
use zkmc_symbolic::utils::*;

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
struct ComputedValues {
    A_s_T_lambda_s: Vec<Vec<i64>>,
    neg_b_s_T_lambda_s: i64,
    neg_h_p_T_mu_s: i64,
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
struct Matrices {
    A_s: Vec<Vec<i64>>,
    b_s: Vec<Vec<i64>>,
    G_p: Vec<Vec<i64>>,
    h_p: Vec<Vec<i64>>,
    #[serde(flatten)]
    others: HashMap<String, Value>,
}

fn analyze_obligations(_c: &mut Criterion) {
    let path_to_file_g = "input/".to_string();
    let candidate_files = vec![
        "test.json".to_string(),
        "exb_i4a2.json".to_string(),
        "rr_2.json".to_string(),
        "dhcp_noOFF_7_2_7.json".to_string(),
    ];

    for input_file in candidate_files {
        println!();
        let input_str = path_to_file_g.clone() + &input_file.clone();
        let file = File::open(input_str.clone()).expect("Error opening file");
        let input: InputParams = serde_json::from_reader(file).expect("Failed to parse JSON");

        let total = input.obligations.len();
        let mut unique_A: HashSet<Vec<Vec<i64>>> = HashSet::new();
        let mut unique_b: HashSet<Vec<Vec<i64>>> = HashSet::new();
        let mut unique_G: HashSet<Vec<Vec<i64>>> = HashSet::new();
        let mut unique_h: HashSet<Vec<Vec<i64>>> = HashSet::new();
        let mut unique_lambda: HashSet<Vec<Vec<i64>>> = HashSet::new();
        let mut unique_mu: HashSet<Vec<Vec<i64>>> = HashSet::new();
        let mut unique_e1: HashSet<Vec<Vec<i64>>> = HashSet::new();
        let mut unique_e2: HashSet<i64> = HashSet::new();
        let mut unique_e3: HashSet<i64> = HashSet::new();
        let mut unique_e1e3: HashSet<(Vec<Vec<i64>>, i64)> = HashSet::new();
        let mut unique_e2e3: HashSet<(i64, i64)> = HashSet::new();
        let mut unique_GpT_mu_e1e3: HashSet<(Vec<Vec<i64>>, Vec<Vec<i64>>, Vec<Vec<i64>>, i64)> = HashSet::new();
        let mut unique_obl: HashSet<(Vec<Vec<i64>>, Vec<Vec<i64>>, Vec<Vec<i64>>, Vec<Vec<i64>>, Vec<Vec<i64>>, Vec<Vec<i64>>)> = HashSet::new();
        let mut unique_A_lambda: HashSet<(Vec<Vec<i64>>, Vec<Vec<i64>>)> = HashSet::new();
        let mut unique_b_lambda: HashSet<(Vec<Vec<i64>>, Vec<Vec<i64>>)> = HashSet::new();
        let mut unique_h_mu: HashSet<(Vec<Vec<i64>>, Vec<Vec<i64>>)> = HashSet::new();
        let mut unique_equals: HashSet<(Vec<Vec<i64>>, Vec<Vec<i64>>, Vec<Vec<i64>>, Vec<Vec<i64>>, Vec<Vec<i64>>)> = HashSet::new();
        let mut unique_delta: HashSet<(Vec<Vec<i64>>, Vec<Vec<i64>>, Vec<Vec<i64>>, Vec<Vec<i64>>)> = HashSet::new();

        for obligation in input.obligations.iter() {
            let A_s = pad_matrix(&obligation.matrices.A_s);
            let b_s = pad_matrix(&obligation.matrices.b_s);
            let G_p = pad_matrix(&obligation.matrices.G_p);
            let h_p = pad_matrix(&obligation.matrices.h_p);
            let lambda_s = pad_matrix(&obligation.witness.lambda_s);
            let mu_s = pad_matrix(&obligation.witness.mu_s);
            let e1 = pad_matrix(&obligation.computed_values.A_s_T_lambda_s);
            let e2 = obligation.computed_values.neg_b_s_T_lambda_s;
            let e3 = obligation.computed_values.neg_h_p_T_mu_s;
            unique_A.insert(A_s.clone());
            unique_b.insert(b_s.clone());
            unique_G.insert(G_p.clone());
            unique_h.insert(h_p.clone());
            unique_lambda.insert(lambda_s.clone());
            unique_mu.insert(mu_s.clone());
            unique_e1.insert(e1.clone());
            unique_e2.insert(e2);
            unique_e3.insert(e3);
            unique_e1e3.insert((e1.clone(), e3));
            unique_e2e3.insert((e2, e3));
            let A_s_T = pad_matrix(&transpose_matrix(&obligation.matrices.A_s));
            let neg_b_s_T = pad_matrix(&transpose_matrix(&negate_matrix(&obligation.matrices.b_s)));
            let G_p_T = pad_matrix(&transpose_matrix(&obligation.matrices.G_p));
            let h_p_T = pad_matrix(&transpose_matrix(&obligation.matrices.h_p));
            unique_GpT_mu_e1e3.insert((G_p_T.clone(), mu_s.clone(), e1, e3));
            unique_A_lambda.insert((A_s_T.clone(), lambda_s.clone()));
            unique_b_lambda.insert((neg_b_s_T.clone(), lambda_s.clone()));
            unique_h_mu.insert((h_p_T.clone(), mu_s.clone()));
            unique_equals.insert((A_s_T, G_p_T, h_p_T.clone(), lambda_s.clone(), mu_s.clone()));
            unique_delta.insert((neg_b_s_T, lambda_s.clone(), h_p_T, mu_s.clone()));
            unique_obl.insert((A_s, b_s, G_p, h_p, lambda_s, mu_s));
        }

        println!("File: {}", input_file);
        println!("  Obligations: {}", total);
        println!(
            "  Unique A_s: {}, Unique b_s: {}, Unique G_p: {}, Unique h_p: {}, Unique lambda_s: {}, Unique mu_s: {}, Unique A_s*lambda_s: {}, Unique b_s*lambda_s: {}, Unique -hp*mu_s: {}, Unique (e1,e3): {}, Unique (e2,e3): {}, Unique (GpT,mu,e1,e3): {}, Unique full obl: {}",
            unique_A.len(),
            unique_b.len(),
            unique_G.len(),
            unique_h.len(),
            unique_lambda.len(),
            unique_mu.len(),
            unique_e1.len(),
            unique_e2.len(),
            unique_e3.len(),
            unique_e1e3.len(),
            unique_e2e3.len(),
            unique_GpT_mu_e1e3.len(),
            unique_obl.len(),
        );
        println!(
            "  Ratio A_s: {}/{} ({:.1}%), Ratio b_s: {}/{} ({:.1}%), Ratio G_p: {}/{} ({:.1}%), Ratio h_p: {}/{} ({:.1}%), Ratio lambda_s: {}/{} ({:.1}%), Ratio mu_s: {}/{} ({:.1}%), Ratio A_s*lambda_s: {}/{} ({:.1}%), Ratio b_s*lambda_s: {}/{} ({:.1}%), Ratio -hp*mu_s: {}/{} ({:.1}%), Ratio (e1,e3): {}/{} ({:.1}%), Ratio (e2,e3): {}/{} ({:.1}%), Ratio (GpT,mu,e1,e3): {}/{} ({:.1}%), Ratio full: {}/{} ({:.1}%)",
            unique_A.len(), total, (unique_A.len() as f64 / total as f64) * 100.0,
            unique_b.len(), total, (unique_b.len() as f64 / total as f64) * 100.0,
            unique_G.len(), total, (unique_G.len() as f64 / total as f64) * 100.0,
            unique_h.len(), total, (unique_h.len() as f64 / total as f64) * 100.0,
            unique_lambda.len(), total, (unique_lambda.len() as f64 / total as f64) * 100.0,
            unique_mu.len(), total, (unique_mu.len() as f64 / total as f64) * 100.0,
            unique_e1.len(), total, (unique_e1.len() as f64 / total as f64) * 100.0,
            unique_e2.len(), total, (unique_e2.len() as f64 / total as f64) * 100.0,
            unique_e3.len(), total, (unique_e3.len() as f64 / total as f64) * 100.0,
            unique_e1e3.len(), total, (unique_e1e3.len() as f64 / total as f64) * 100.0,
            unique_e2e3.len(), total, (unique_e2e3.len() as f64 / total as f64) * 100.0,
            unique_GpT_mu_e1e3.len(), total, (unique_GpT_mu_e1e3.len() as f64 / total as f64) * 100.0,
            unique_obl.len(), total, (unique_obl.len() as f64 / total as f64) * 100.0,
        );
        println!("  Cache-size predictions:");
        println!(
            "    e1 zkmm   (A,lambda):        {}/{} ({:.1}%)",
            unique_A_lambda.len(), total, (unique_A_lambda.len() as f64 / total as f64) * 100.0,
        );
        println!(
            "    e2 zkmm   (b,lambda):        {}/{} ({:.1}%)",
            unique_b_lambda.len(), total, (unique_b_lambda.len() as f64 / total as f64) * 100.0,
        );
        println!(
            "    e3 comm   (h,mu):            {}/{} ({:.1}%)",
            unique_h_mu.len(), total, (unique_h_mu.len() as f64 / total as f64) * 100.0,
        );
        println!(
            "    equals    (A,G,h,lambda,mu): {}/{} ({:.1}%)",
            unique_equals.len(), total, (unique_equals.len() as f64 / total as f64) * 100.0,
        );
        println!(
            "    delta     (b,lambda,h,mu):   {}/{} ({:.1}%)",
            unique_delta.len(), total, (unique_delta.len() as f64 / total as f64) * 100.0,
        );
    }
}

criterion_group!(benches, analyze_obligations);
criterion_main!(benches);
