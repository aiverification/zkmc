use crate::interpolation::*;
use ark_bls12_381::Bls12_381 as bls;
use ark_ec::pairing::Pairing;
use ark_ff::Field;
use ark_poly::{Polynomial, univariate::DensePolynomial};
use ark_poly_commit::kzg10::{Commitment, KZG10, Powers, Proof, Randomness, VerifierKey};
use ark_std::test_rng;
use rayon::prelude::*;
use std::{
    sync::atomic::{AtomicBool, Ordering},
    time::Instant,
};

pub type F = <bls as Pairing>::ScalarField;
type UniPoly_381 = ark_poly::univariate::DensePolynomial<F>;

pub struct ZkpProof {
    pub comm_p_S: Commitment<bls>,
    pub p_S_coset_offset: F,
    pub p_S_group_gen: F,
    pub E_init_proofs: Vec<Proof<bls>>,
    pub comm_p_T: Commitment<bls>,
    pub p_T_coset_offset: F,
    pub p_T_group_gen: F,
    pub E_step_fairstep_proofs: Vec<Proof<bls>>,
    pub vk: VerifierKey<bls>,
}

// Returns proof (if valid) and the time taken to set up the KZG parameters
pub fn prove(
    E_init: &Vec<u64>,
    E_step: &Vec<u64>,
    E_fairstep: &Vec<u64>,
    E_S0: &Vec<u64>,
    E_T: &Vec<u64>,
    num_states: usize,
    num_transitions: usize,
    timer: &Instant,
    time_limit: u64,
) -> (Option<ZkpProof>, u128) {
    println!("================ Merging E_step and E_fairstep ================");
    let mut E_step_fairstep: Vec<u64> = vec![];
    for e in E_step.iter() {
        E_step_fairstep.push(*e);
    }
    for e in E_fairstep.iter() {
        if !E_step_fairstep.contains(e) {
            E_step_fairstep.push(*e);
        }
    }

    println!("================ Calculating p_S_0 ================");
    // Create vector of 1s and 0s depending on whether s \in S_0, then interpolate
    let mut p_S_values: Vec<F> = vec![F::from(0u64); num_states];
    for s in E_S0.iter() {
        p_S_values[*s as usize] = F::from(1u64);
    }
    let (p_S, p_S_coset_offset, p_S_group_gen) = interpolate(&p_S_values);
    let p_S_degree = p_S.degree();

    println!("================ Calculating p_T ================");
    // Create vector of 1s and 0s depending on whether (s, s') \in T, then interpolate
    let mut p_T_values: Vec<F> = vec![F::from(0u64); num_transitions];
    for t in E_T.iter() {
        p_T_values[*t as usize] = F::from(1u64);
    }
    let (p_T, p_T_coset_offset, p_T_group_gen) = interpolate(&p_T_values);
    let p_T_degree = p_T.degree();

    let max_degree: usize;
    if p_S_degree >= p_T_degree {
        max_degree = p_S_degree;
    } else {
        max_degree = p_T_degree;
    }

    let setup_timer = Instant::now();
    println!("================ Generating KZG parameters ================");
    let rng = &mut test_rng();
    let params =
        KZG10::<bls, UniPoly_381>::setup(max_degree, false, rng).expect("KZG Setup failed");
    let powers_of_g = params.powers_of_g[..=max_degree].to_vec();
    let powers_of_gamma_g = (0..=max_degree)
        .map(|i| params.powers_of_gamma_g[&i])
        .collect();
    let powers: Powers<'_, bls> = Powers {
        powers_of_g: ark_std::borrow::Cow::Owned(powers_of_g),
        powers_of_gamma_g: ark_std::borrow::Cow::Owned(powers_of_gamma_g),
    };
    let setup_elapsed = setup_timer.elapsed().as_millis();

    let vk: VerifierKey<bls> = VerifierKey {
        g: params.powers_of_g[0],
        gamma_g: params.powers_of_gamma_g[&0],
        h: params.h,
        beta_h: params.beta_h,
        prepared_h: params.prepared_h.clone(),
        prepared_beta_h: params.prepared_beta_h.clone(),
    };

    // Commit to both polynomials
    let (comm_p_S, rand_p_S, comm_p_T, rand_p_T) =
        commit_to_polys(&powers, &p_S, &p_T, &Some(1usize));

    // Prove all points on p_S (E_init) and p_T (E_step_fairstep)
    let E_init_proofs_opt = prove_on_poly(
        &E_init,
        &powers,
        &p_S,
        &rand_p_S,
        p_S_coset_offset.clone(),
        p_S_group_gen.clone(),
        timer,
        time_limit,
    );
    if E_init_proofs_opt.is_none() {
        return (None, setup_elapsed);
    }
    let E_init_proofs = E_init_proofs_opt.unwrap();
    let E_step_fairstep_proofs_opt = prove_on_poly(
        &E_step_fairstep,
        &powers,
        &p_T,
        &rand_p_T,
        p_T_coset_offset.clone(),
        p_T_group_gen.clone(),
        timer,
        time_limit,
    );
    if E_step_fairstep_proofs_opt.is_none() {
        return (None, setup_elapsed);
    }
    let E_step_fairstep_proofs = E_step_fairstep_proofs_opt.unwrap();

    return (
        Some(ZkpProof {
            comm_p_S,
            p_S_coset_offset,
            p_S_group_gen,
            E_init_proofs,
            comm_p_T,
            p_T_coset_offset,
            p_T_group_gen,
            E_step_fairstep_proofs,
            vk,
        }),
        setup_elapsed,
    );
}

impl ZkpProof {
    pub fn verify(
        &self,
        E_init: &Vec<u64>,
        E_step: &Vec<u64>,
        E_fairstep: &Vec<u64>,
        timer: &Instant,
        time_limit: u64,
    ) -> Option<bool> {
        println!("================ Merging E_step and E_fairstep ================");
        let mut E_step_fairstep: Vec<u64> = vec![];
        for e in E_step.iter() {
            E_step_fairstep.push(*e);
        }
        for e in E_fairstep.iter() {
            if !E_step_fairstep.contains(e) {
                E_step_fairstep.push(*e);
            }
        }

        let mut verifier_rng = rand::thread_rng();

        // Verify points on p_S
        let E_init_comms = vec![self.comm_p_S; E_init.len()];
        let E_init_points: Vec<F> = E_init
            .iter()
            .map(|e| self.p_S_coset_offset * self.p_S_group_gen.pow(&[*e as u64]))
            .collect();
        let E_init_values = vec![F::from(0u64); E_init.len()]; // All points should open to 0
        let E_init_checked = KZG10::<bls, DensePolynomial<F>>::batch_check(
            &self.vk,
            &E_init_comms,
            &E_init_points,
            &E_init_values,
            &self.E_init_proofs,
            &mut verifier_rng,
        )
        .expect("Error checking E_init");
        println!("Verified E_init: {:?}", E_init_checked);
        if timer.elapsed().as_secs() > time_limit {
            return None;
        }

        // Verify points on p_T
        let E_step_fairstep_comms = vec![self.comm_p_T; E_step_fairstep.len()];
        let E_step_fairstep_points: Vec<F> = E_step_fairstep
            .iter()
            .map(|e| self.p_T_coset_offset * self.p_T_group_gen.pow(&[*e as u64]))
            .collect();
        let E_step_fairstep_values = vec![F::from(0u64); E_step_fairstep.len()];
        let E_step_fairstep_checked = KZG10::<bls, DensePolynomial<F>>::batch_check(
            &self.vk,
            &E_step_fairstep_comms,
            &E_step_fairstep_points,
            &E_step_fairstep_values,
            &self.E_step_fairstep_proofs,
            &mut verifier_rng,
        )
        .expect("Error checking E_step_fairstep");
        println!("Verified E_step_fairstep: {:?}", E_step_fairstep_checked);
        if timer.elapsed().as_secs() > time_limit {
            return None;
        }

        return Some(E_init_checked && E_step_fairstep_checked);
    }
}

// Helper function to commit to two polynomials p_S and p_T
pub fn commit_to_polys(
    powers: &Powers<'_, bls>,
    p_S: &DensePolynomial<F>,
    p_T: &DensePolynomial<F>,
    hiding_bound: &Option<usize>,
) -> (
    Commitment<bls>,
    Randomness<F, UniPoly_381>,
    Commitment<bls>,
    Randomness<F, UniPoly_381>,
) {
    println!("================ Calculating commitment to p_S_0 ================");
    let mut poly_rng = rand::thread_rng();
    let (comm_p_S, rand_p_S) =
        KZG10::<bls, UniPoly_381>::commit(&powers, &p_S, hiding_bound.clone(), Some(&mut poly_rng))
            .expect("Commitment to p_S failed");

    println!("================ Calculating commitment to p_T ================");
    let (comm_p_T, rand_p_T) =
        KZG10::<bls, UniPoly_381>::commit(&powers, &p_T, hiding_bound.clone(), Some(&mut poly_rng))
            .expect("Commitment to p_T failed");

    return (comm_p_S, rand_p_S, comm_p_T, rand_p_T);
}

// Prove all points on a polynomial in parallel with time checking (for OOT) across threads
pub fn prove_on_poly(
    embeddings: &Vec<u64>,
    powers: &Powers<'_, bls>,
    poly: &DensePolynomial<F>,
    rand: &Randomness<F, UniPoly_381>,
    poly_coset_offset: F,
    poly_group_gen: F,
    time: &Instant,
    time_limit: u64,
) -> Option<Vec<Proof<bls>>> {
    let embedding_points: Vec<F> = embeddings
        .iter()
        .map(|e| poly_coset_offset * poly_group_gen.pow(&[*e as u64]))
        .collect();
    let timed_out = AtomicBool::new(false);
    let results: Vec<Option<Proof<bls>>> = embedding_points
        .par_iter()
        .enumerate()
        .map(|(i, e)| {
            if timed_out.load(Ordering::Relaxed) {
                return None;
            }
            // Each thread checks its own slice of indices
            if i % 500 == 0 && time.elapsed().as_secs() > time_limit {
                timed_out.store(true, Ordering::Relaxed);
                return None;
            }
            Some(
                KZG10::open(&powers, poly, *e, rand).expect("Failed to prove e in E_step_fairstep"),
            )
        })
        .collect();
    if results.iter().any(|r| r.is_none()) {
        return None;
    }

    Some(results.into_iter().flatten().collect())
}
