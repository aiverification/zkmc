use crate::interpolation::*;
use ark_bls12_381::Bls12_381 as bls;
use ark_ec::{CurveGroup, VariableBaseMSM, pairing::Pairing};
use ark_ff::{Field, One, UniformRand, Zero};
use ark_poly::{
    DenseUVPolynomial, EvaluationDomain, GeneralEvaluationDomain, Polynomial,
    univariate::{DenseOrSparsePolynomial, DensePolynomial},
};
use ark_poly_commit::kzg10::{Commitment, KZG10, Powers};
use ark_std::test_rng;
use std::{collections::HashSet, time::Instant};

pub type F = <bls as Pairing>::ScalarField;
type UniPoly_381 = ark_poly::univariate::DensePolynomial<F>;
type G2Affine = <bls as Pairing>::G2Affine;

// Degree of the random blinding polynomial rho. We commit to p + Z_H * rho, where Z_H is
// the vanishing polynomial of the interpolation domain, so every evaluation on the domain
// is unchanged while the commitment hides the unopened evaluations. The verifier only ever
// sees one independent evaluation of the blinded polynomial per set (the quotient
// commitment is determined by the commitment and the public Z_E), so a small constant
// degree suffices heuristically; the formal zk analysis is still to be done.
const BLINDING_DEGREE: usize = 2;

pub struct ZkpProof {
    pub comm_p_S: Commitment<bls>,
    pub p_S_coset_offset: F,
    pub p_S_group_gen: F,
    pub E_init_proof: Commitment<bls>,
    pub comm_p_T: Commitment<bls>,
    pub p_T_coset_offset: F,
    pub p_T_group_gen: F,
    pub E_step_fairstep_proof: Commitment<bls>,
    pub neg_powers_of_h: Vec<G2Affine>,
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
    // Deduplicate within the sets as well as across them: repeated points would give the
    // vanishing polynomial Z_E repeated roots, which p_S/p_T are not divisible by.
    let E_step_fairstep = merge_dedup(E_step, E_fairstep);
    let E_init_dedup = merge_dedup(E_init, &[]);

    println!("================ Calculating p_S_0 ================");
    // Create vector of 1s and 0s depending on whether s \in S_0, then interpolate
    let mut p_S_values: Vec<F> = vec![F::from(0u64); num_states];
    for s in E_S0.iter() {
        p_S_values[*s as usize] = F::from(1u64);
    }
    let (p_S, p_S_coset_offset, p_S_group_gen) = interpolate(&p_S_values);

    println!("================ Calculating p_T ================");
    // Create vector of 1s and 0s depending on whether (s, s') \in T, then interpolate
    let mut p_T_values: Vec<F> = vec![F::from(0u64); num_transitions];
    for t in E_T.iter() {
        p_T_values[*t as usize] = F::from(1u64);
    }
    let (p_T, p_T_coset_offset, p_T_group_gen) = interpolate(&p_T_values);

    println!("================ Blinding p_S_0 and p_T ================");
    let blinding_rng = &mut rand::thread_rng();
    let p_S = blind_over_domain(p_S, num_states, blinding_rng);
    let p_T = blind_over_domain(p_T, num_transitions, blinding_rng);
    let p_S_degree = p_S.degree();
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
    // produce_g2_powers gives us h^{beta^-i}, which the verifier needs to check
    // batched openings (see batch_check_zero)
    let params =
        KZG10::<bls, UniPoly_381>::setup(max_degree, true, rng).expect("KZG Setup failed");
    let powers_of_g = params.powers_of_g[..=max_degree].to_vec();
    let powers_of_gamma_g = (0..=max_degree)
        .map(|i| params.powers_of_gamma_g[&i])
        .collect();
    let powers: Powers<'_, bls> = Powers {
        powers_of_g: ark_std::borrow::Cow::Owned(powers_of_g),
        powers_of_gamma_g: ark_std::borrow::Cow::Owned(powers_of_gamma_g),
    };
    let neg_powers_of_h: Vec<G2Affine> = (0..=max_degree)
        .map(|i| params.neg_powers_of_h[&i])
        .collect();
    let setup_elapsed = setup_timer.elapsed().as_millis();

    // Commit to both polynomials
    let (comm_p_S, comm_p_T) = commit_to_polys(&powers, &p_S, &p_T);

    // Prove all points of E_init on p_S and E_step_fairstep on p_T, with one batched
    // opening proof per set
    println!("================ Proving E_init on p_S_0 ================");
    let E_init_points = to_points(&E_init_dedup, p_S_coset_offset, p_S_group_gen);
    let E_init_proof = batch_open_zero(&powers, &p_S, &E_init_points, &comm_p_S);
    if timer.elapsed().as_secs() > time_limit {
        return (None, setup_elapsed);
    }
    println!("================ Proving E_step_fairstep on p_T ================");
    let E_step_fairstep_points = to_points(&E_step_fairstep, p_T_coset_offset, p_T_group_gen);
    let E_step_fairstep_proof = batch_open_zero(&powers, &p_T, &E_step_fairstep_points, &comm_p_T);
    if timer.elapsed().as_secs() > time_limit {
        return (None, setup_elapsed);
    }

    return (
        Some(ZkpProof {
            comm_p_S,
            p_S_coset_offset,
            p_S_group_gen,
            E_init_proof,
            comm_p_T,
            p_T_coset_offset,
            p_T_group_gen,
            E_step_fairstep_proof,
            neg_powers_of_h,
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
        // Must match the prover's deduplication so both sides build the same Z_E
        let E_step_fairstep = merge_dedup(E_step, E_fairstep);
        let E_init_dedup = merge_dedup(E_init, &[]);

        // Verify points on p_S
        let E_init_points = to_points(&E_init_dedup, self.p_S_coset_offset, self.p_S_group_gen);
        let E_init_checked = batch_check_zero(
            &self.neg_powers_of_h,
            &self.comm_p_S,
            &self.E_init_proof,
            &E_init_points,
        );
        println!("Verified E_init: {:?}", E_init_checked);
        if timer.elapsed().as_secs() > time_limit {
            return None;
        }

        // Verify points on p_T
        let E_step_fairstep_points =
            to_points(&E_step_fairstep, self.p_T_coset_offset, self.p_T_group_gen);
        let E_step_fairstep_checked = batch_check_zero(
            &self.neg_powers_of_h,
            &self.comm_p_T,
            &self.E_step_fairstep_proof,
            &E_step_fairstep_points,
        );
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
) -> (Commitment<bls>, Commitment<bls>) {
    println!("================ Calculating commitment to p_S_0 ================");
    let (comm_p_S, _) =
        KZG10::<bls, UniPoly_381>::commit(&powers, &p_S, None, None).expect("Commitment to p_S failed");

    println!("================ Calculating commitment to p_T ================");
    let (comm_p_T, _) =
        KZG10::<bls, UniPoly_381>::commit(&powers, &p_T, None, None).expect("Commitment to p_T failed");

    return (comm_p_S, comm_p_T);
}

// Prove that poly evaluates to 0 at every point in points with a single group element:
// since poly vanishes on all of E, it is divisible by Z_E(X) = prod_{e in E} (X - e), and
// the proof is the commitment to the quotient q(X) = poly(X) / Z_E(X). This is the batch
// opening of the KZG10 paper (Section 3.4), specialised to all-zero values.
pub fn batch_open_zero(
    powers: &Powers<'_, bls>,
    poly: &DensePolynomial<F>,
    points: &[F],
    comm: &Commitment<bls>,
) -> Commitment<bls> {
    if points.is_empty() {
        // Z_E = 1 and q = poly, so the proof is the commitment itself
        return *comm;
    }
    let Z_E = vanishing_poly(points);
    let (q, rem) = DenseOrSparsePolynomial::from(poly)
        .divide_with_q_and_r(&DenseOrSparsePolynomial::from(&Z_E))
        .expect("Division by Z_E failed");
    assert!(rem.is_zero(), "Polynomial does not vanish on all points");
    let (w, _) =
        KZG10::<bls, UniPoly_381>::commit(powers, &q, None, None).expect("Commitment to quotient failed");
    return w;
}

// Check a batched opening proof, i.e. that the polynomial behind comm evaluates to 0 on
// all of points. The standard check would be e(C, h) == e(W, h^{Z_E(beta)}), but our SRS
// only contains the negative powers h^{beta^-i}, so we scale both sides by beta^-m
// (m = deg Z_E) and check e(C, h^{beta^-m}) == e(W, h^{Z_E(beta) * beta^-m}) instead,
// where h^{Z_E(beta) * beta^-m} = prod_j (h^{beta^-j})^{z_{m-j}}.
pub fn batch_check_zero(
    neg_powers_of_h: &[G2Affine],
    comm: &Commitment<bls>,
    proof: &Commitment<bls>,
    points: &[F],
) -> bool {
    let Z_E = vanishing_poly(points);
    let m = Z_E.degree();
    assert!(m < neg_powers_of_h.len(), "Not enough negative powers of h");
    let scalars: Vec<F> = Z_E.coeffs.iter().rev().copied().collect();
    let D = <bls as Pairing>::G2::msm(&neg_powers_of_h[..=m], &scalars)
        .expect("MSM failed")
        .into_affine();
    return bls::pairing(comm.0, neg_powers_of_h[m]) == bls::pairing(proof.0, D);
}

// Z_E(X) = prod_{e in E} (X - e), built as a balanced product tree so that the FFT-based
// polynomial multiplication keeps this O(m log^2 m) instead of O(m^2)
pub fn vanishing_poly(points: &[F]) -> DensePolynomial<F> {
    match points.len() {
        0 => DensePolynomial::from_coefficients_vec(vec![F::one()]),
        1 => DensePolynomial::from_coefficients_vec(vec![-points[0], F::one()]),
        n => &vanishing_poly(&points[..n / 2]) * &vanishing_poly(&points[n / 2..]),
    }
}

// Merge two embedding vectors, dropping duplicates within and across them
pub fn merge_dedup(a: &[u64], b: &[u64]) -> Vec<u64> {
    let mut seen = HashSet::new();
    a.iter().chain(b.iter()).copied().filter(|e| seen.insert(*e)).collect()
}

// Map embedding indices to their evaluation points on the domain
fn to_points(embeddings: &[u64], coset_offset: F, group_gen: F) -> Vec<F> {
    embeddings
        .iter()
        .map(|e| coset_offset * group_gen.pow(&[*e]))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_valid_and_rejects_invalid_openings() {
        let timer = Instant::now();
        // S_0 = {0}, T = {0, 1}; all opened embeddings lie outside these sets
        let E_init = vec![1u64];
        let E_step = vec![2u64];
        let E_fairstep = vec![3u64];
        let E_S0 = vec![0u64];
        let E_T = vec![0u64, 1u64];
        let (proof, _) = prove(
            &E_init, &E_step, &E_fairstep, &E_S0, &E_T, 4, 8, &timer, 3600,
        );
        let proof = proof.unwrap();
        assert_eq!(
            proof.verify(&E_init, &E_step, &E_fairstep, &timer, 3600),
            Some(true)
        );
        // Claiming p_S_0 opens to 0 at state 0 must fail: state 0 is in S_0
        assert_eq!(
            proof.verify(&vec![0u64], &E_step, &E_fairstep, &timer, 3600),
            Some(false)
        );
        // Verifying against a different point set than was proven must also fail
        assert_eq!(
            proof.verify(&E_init, &E_step, &vec![3u64, 4u64], &timer, 3600),
            Some(false)
        );
    }
}

// Add Z_H(X) * rho(X) for a random rho of degree BLINDING_DEGREE, where Z_H is the
// vanishing polynomial of the interpolation domain for num_values values. This leaves
// every evaluation on the domain unchanged, so divisibility by Z_E is preserved.
fn blind_over_domain<R: rand::RngCore>(
    p: DensePolynomial<F>,
    num_values: usize,
    rng: &mut R,
) -> DensePolynomial<F> {
    let domain =
        GeneralEvaluationDomain::<F>::new(num_values).expect("no domain of this size");
    let n = domain.size();
    // Z_H(X) = X^n - offset^n
    let offset_pow_n = domain.coset_offset_pow_size();
    let mut coeffs = p.coeffs;
    coeffs.resize(n + BLINDING_DEGREE + 1, F::zero());
    for i in 0..=BLINDING_DEGREE {
        let r = F::rand(rng);
        coeffs[n + i] += r;
        coeffs[i] -= offset_pow_n * r;
    }
    return DensePolynomial::from_coefficients_vec(coeffs);
}
