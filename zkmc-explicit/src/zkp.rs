use crate::interpolation::*;
use ark_bls12_381::Bls12_381 as bls;
use ark_ec::{CurveGroup, VariableBaseMSM, pairing::Pairing};
use ark_ff::{FftField, Field, One, UniformRand, Zero, batch_inversion};
use ark_poly::{
    DenseUVPolynomial, EvaluationDomain, GeneralEvaluationDomain, Polynomial,
    univariate::DensePolynomial,
};
use ark_poly_commit::kzg10::{Commitment, KZG10, Powers};
use ark_std::test_rng;
use rayon::prelude::*;
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

// Below this many roots, the vanishing polynomial is built by multiplying in the linear
// factors one at a time; above it, by a balanced product tree of FFT multiplications. The
// crossover is where the FFT's setup cost stops paying for itself.
const PRODUCT_TREE_LEAF: usize = 64;

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
    stage("Merging E_step and E_fairstep", timer);
    // Deduplicate within the sets as well as across them: repeated points would give the
    // vanishing polynomial Z_E repeated roots, which p_S/p_T are not divisible by.
    let E_step_fairstep = merge_dedup(E_step, E_fairstep);
    let E_init_dedup = merge_dedup(E_init, &[]);

    stage("Calculating p_S_0", timer);
    // Create vector of 1s and 0s depending on whether s \in S_0, then interpolate
    let mut p_S_values: Vec<F> = vec![F::from(0u64); num_states];
    for s in E_S0.iter() {
        p_S_values[*s as usize] = F::from(1u64);
    }
    let (p_S, p_S_coset_offset, p_S_group_gen) = interpolate(&p_S_values);

    stage("Calculating p_T", timer);
    // Create vector of 1s and 0s depending on whether (s, s') \in T, then interpolate
    let mut p_T_values: Vec<F> = vec![F::from(0u64); num_transitions];
    for t in E_T.iter() {
        p_T_values[*t as usize] = F::from(1u64);
    }
    let (p_T, p_T_coset_offset, p_T_group_gen) = interpolate(&p_T_values);

    stage("Blinding p_S_0 and p_T", timer);
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
    stage("Generating KZG parameters", timer);
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
    stage("Committing to p_S_0 and p_T", timer);
    let (comm_p_S, comm_p_T) = commit_to_polys(&powers, &p_S, &p_T);

    // Prove all points of E_init on p_S and E_step_fairstep on p_T, with one batched
    // opening proof per set
    stage("Proving E_init on p_S_0", timer);
    let E_init_proof = batch_open_zero(&powers, &p_S, &E_init_dedup, num_states, &comm_p_S);
    if timer.elapsed().as_secs() > time_limit {
        return (None, setup_elapsed);
    }
    stage("Proving E_step_fairstep on p_T", timer);
    let E_step_fairstep_proof =
        batch_open_zero(&powers, &p_T, &E_step_fairstep, num_transitions, &comm_p_T);
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
        stage("Merging E_step and E_fairstep", timer);
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
    let (comm_p_S, _) =
        KZG10::<bls, UniPoly_381>::commit(&powers, &p_S, None, None).expect("Commitment to p_S failed");

    let (comm_p_T, _) =
        KZG10::<bls, UniPoly_381>::commit(&powers, &p_T, None, None).expect("Commitment to p_T failed");

    return (comm_p_S, comm_p_T);
}

// Prove that poly evaluates to 0 at every embedding in embeddings with a single group
// element: since poly vanishes on all of E, it is divisible by Z_E(X) = prod_{e in E}
// (X - e), and the proof is the commitment to the quotient q(X) = poly(X) / Z_E(X). This is
// the batch opening of the KZG10 paper (Section 3.4), specialised to all-zero values.
//
// num_values is the number of interpolated values behind poly, i.e. it fixes the
// interpolation domain H that the embeddings index into.
pub fn batch_open_zero(
    powers: &Powers<'_, bls>,
    poly: &DensePolynomial<F>,
    embeddings: &[u64],
    num_values: usize,
    comm: &Commitment<bls>,
) -> Commitment<bls> {
    if embeddings.is_empty() {
        // Z_E = 1 and q = poly, so the proof is the commitment itself
        return *comm;
    }
    let q = divide_by_vanishing(poly, embeddings, num_values);
    let (w, _) =
        KZG10::<bls, UniPoly_381>::commit(powers, &q, None, None).expect("Commitment to quotient failed");
    return w;
}

// q(X) = poly(X) / Z_E(X), an exact division.
//
// Doing this with a long division and a flat product of linear factors — the obvious way —
// costs Theta(|E| * (deg poly - |E|)) and Theta(|E|^2) single-threaded field operations
// respectively, which at |E| ~ n dominates every group operation in the prover by orders of
// magnitude. Instead:
//
//   * E is a subset of the interpolation domain H, so Z_E * Z_{H\E} = Z_H(X) = X^n - c^n
//     for the domain offset c. We therefore build the vanishing polynomial of whichever of
//     E and H\E has fewer elements (as a product tree, O(k log^2 k)), and when it is the
//     complement we divide by Z_H instead — which is two terms and free to evaluate.
//   * the division itself is a pointwise division of evaluations on a coset of a domain
//     large enough to hold poly. The coset is generated by a multiplicative generator of
//     the whole field, so it is disjoint from H and the divisor has no zeros on it.
//
// Both are O(n log n) and parallel.
fn divide_by_vanishing(
    poly: &DensePolynomial<F>,
    embeddings: &[u64],
    num_values: usize,
) -> DensePolynomial<F> {
    let domain =
        GeneralEvaluationDomain::<F>::new(num_values).expect("no domain of this size");
    let n = domain.size();
    let m = embeddings.len();
    assert!(m <= n, "more opened points than domain elements");
    assert!(
        embeddings.iter().all(|e| (*e as usize) < n),
        "embedding outside the interpolation domain"
    );
    assert!(poly.degree() >= m, "polynomial has lower degree than Z_E");
    let deg_q = poly.degree() - m;

    if 2 * m <= n {
        let Z_E = vanishing_poly(&to_points(
            embeddings,
            domain.coset_offset(),
            domain.group_gen(),
        ));
        return divide_on_coset(poly, &Z_E, deg_q);
    }

    // |E| is more than half the domain, so Z_{H\E} is the cheaper factor to build
    let mut in_E = vec![false; n];
    for e in embeddings.iter() {
        in_E[*e as usize] = true;
    }
    let complement: Vec<u64> = (0..n as u64).filter(|i| !in_E[*i as usize]).collect();
    let Z_complement = vanishing_poly(&to_points(
        &complement,
        domain.coset_offset(),
        domain.group_gen(),
    ));
    return divide_by_Z_H_on_coset(poly, &Z_complement, n, domain.coset_offset_pow_size(), deg_q);
}

// A coset of a domain of at least size elements, disjoint from every power-of-two
// multiplicative subgroup (the offset generates the whole multiplicative group).
fn division_coset(size: usize) -> GeneralEvaluationDomain<F> {
    GeneralEvaluationDomain::<F>::new(size)
        .expect("no domain of this size")
        .get_coset(F::GENERATOR)
        .expect("no coset with this offset")
}

// num / den, exact, via pointwise division of coset evaluations
fn divide_on_coset(
    num: &DensePolynomial<F>,
    den: &DensePolynomial<F>,
    deg_q: usize,
) -> DensePolynomial<F> {
    let coset = division_coset(num.degree() + 1);
    let num_evals = coset.fft(&num.coeffs);
    let mut den_evals = coset.fft(&den.coeffs);
    assert!(
        den_evals.iter().all(|e| !e.is_zero()),
        "divisor vanishes on the division coset"
    );
    batch_inversion(&mut den_evals);
    let q_evals: Vec<F> = num_evals
        .par_iter()
        .zip(den_evals.par_iter())
        .map(|(v, inv)| *v * inv)
        .collect();
    return truncated_ifft(&coset, q_evals, deg_q);
}

// num * factor / Z_H, exact, where Z_H(X) = X^n - offset_pow_n vanishes on the whole
// interpolation domain. Z_H's coset evaluations are a geometric progression, so they cost
// one field multiplication each instead of an FFT.
fn divide_by_Z_H_on_coset(
    num: &DensePolynomial<F>,
    factor: &DensePolynomial<F>,
    n: usize,
    offset_pow_n: F,
    deg_q: usize,
) -> DensePolynomial<F> {
    let coset = division_coset(num.degree() + factor.degree() + 1);
    // on the coset point xi*w^i we have (xi*w^i)^n = xi^n * (w^n)^i
    let mut Z_H_evals = Vec::with_capacity(coset.size());
    let mut x_pow_n = coset.coset_offset().pow(&[n as u64]);
    let step = coset.group_gen().pow(&[n as u64]);
    for _ in 0..coset.size() {
        Z_H_evals.push(x_pow_n - offset_pow_n);
        x_pow_n *= step;
    }
    assert!(
        Z_H_evals.iter().all(|e| !e.is_zero()),
        "Z_H vanishes on the division coset"
    );
    batch_inversion(&mut Z_H_evals);
    let num_evals = coset.fft(&num.coeffs);
    let factor_evals = coset.fft(&factor.coeffs);
    let q_evals: Vec<F> = num_evals
        .par_iter()
        .zip(factor_evals.par_iter())
        .zip(Z_H_evals.par_iter())
        .map(|((v, f), inv)| *v * f * inv)
        .collect();
    return truncated_ifft(&coset, q_evals, deg_q);
}

// Interpolate coset evaluations back into coefficients, keeping the deg_q + 1 coefficients
// the quotient is known to have (the rest are zero up to the exactness of the division)
fn truncated_ifft(
    coset: &GeneralEvaluationDomain<F>,
    evals: Vec<F>,
    deg_q: usize,
) -> DensePolynomial<F> {
    let mut coeffs = coset.ifft(&evals);
    assert!(
        coeffs[deg_q + 1..].iter().all(|c| c.is_zero()),
        "division was not exact: polynomial does not vanish on all points"
    );
    coeffs.truncate(deg_q + 1);
    return DensePolynomial::from_coefficients_vec(coeffs);
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
// polynomial multiplication keeps this O(m log^2 m) instead of O(m^2). The two halves are
// independent, so the tree is walked in parallel; below PRODUCT_TREE_LEAF roots the FFT is
// slower than just multiplying the linear factors in.
pub fn vanishing_poly(points: &[F]) -> DensePolynomial<F> {
    if points.len() <= PRODUCT_TREE_LEAF {
        return vanishing_poly_flat(points);
    }
    let n = points.len();
    let (lo, hi) = rayon::join(
        || vanishing_poly(&points[..n / 2]),
        || vanishing_poly(&points[n / 2..]),
    );
    return &lo * &hi;
}

// prod_{e in E} (X - e) by multiplying in one linear factor at a time, O(m^2) but with no
// FFT overhead — only used at the leaves of the product tree
fn vanishing_poly_flat(points: &[F]) -> DensePolynomial<F> {
    let mut coeffs = vec![F::zero(); points.len() + 1];
    coeffs[0] = F::one();
    for (i, e) in points.iter().enumerate() {
        for j in (0..=i).rev() {
            let c = coeffs[j];
            coeffs[j + 1] += c;
            coeffs[j] = -*e * c;
        }
    }
    return DensePolynomial::from_coefficients_vec(coeffs);
}

// Announce a stage together with the elapsed time, so that a log of a run that is still
// going tells you which stage it is in and how long the previous ones took
fn stage(name: &str, timer: &Instant) {
    println!("[{:>10.3?}] ================ {name} ================", timer.elapsed());
}

// Merge two embedding vectors, dropping duplicates within and across them
pub fn merge_dedup(a: &[u64], b: &[u64]) -> Vec<u64> {
    let mut seen = HashSet::new();
    a.iter().chain(b.iter()).copied().filter(|e| seen.insert(*e)).collect()
}

// Map embedding indices to their evaluation points on the domain
fn to_points(embeddings: &[u64], coset_offset: F, group_gen: F) -> Vec<F> {
    embeddings
        .par_iter()
        .map(|e| coset_offset * group_gen.pow(&[*e]))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use ark_poly::univariate::DenseOrSparsePolynomial;

    // Reference implementation: long division by a flat product of linear factors
    fn divide_by_vanishing_naive(
        poly: &DensePolynomial<F>,
        embeddings: &[u64],
        num_values: usize,
    ) -> DensePolynomial<F> {
        let domain = GeneralEvaluationDomain::<F>::new(num_values).unwrap();
        let points = to_points(embeddings, domain.coset_offset(), domain.group_gen());
        let Z_E = vanishing_poly_flat(&points);
        let (q, rem) = DenseOrSparsePolynomial::from(poly)
            .divide_with_q_and_r(&DenseOrSparsePolynomial::from(&Z_E))
            .unwrap();
        assert!(rem.is_zero());
        q
    }

    // A blinded interpolation of values that are 1 on `ones` and 0 everywhere else
    fn test_poly(num_values: usize, ones: &[u64]) -> DensePolynomial<F> {
        let mut values = vec![F::zero(); num_values];
        for i in ones {
            values[*i as usize] = F::one();
        }
        let (p, _, _) = interpolate(&values);
        blind_over_domain(p, num_values, &mut test_rng())
    }

    #[test]
    fn fast_division_matches_long_division() {
        // (num_values, opened embeddings): covers a power-of-two and a non-power-of-two
        // domain, and both the |E| <= n/2 branch and the complement branch
        for num_values in [64usize, 100, 256] {
            let n = GeneralEvaluationDomain::<F>::new(num_values).unwrap().size();
            for m in [1usize, 4, num_values / 2, num_values - 1, num_values] {
                let opened: Vec<u64> = (0..m as u64).collect();
                // the polynomial must vanish on the opened points, so put the ones after them
                let ones: Vec<u64> = (m as u64..num_values as u64).step_by(3).collect();
                let p = test_poly(num_values, &ones);
                let fast = divide_by_vanishing(&p, &opened, num_values);
                let naive = divide_by_vanishing_naive(&p, &opened, num_values);
                assert_eq!(
                    fast, naive,
                    "num_values={num_values} n={n} m={m}: fast division disagrees"
                );
            }
        }
    }

    #[test]
    fn vanishing_poly_matches_flat_product() {
        // needs to be well above PRODUCT_TREE_LEAF to exercise the tree
        let points: Vec<F> = (1..200u64).map(F::from).collect();
        let z = vanishing_poly(&points);
        assert_eq!(z, vanishing_poly_flat(&points));
        // independent of both: it must be monic of degree |E| and vanish on E
        assert_eq!(z.degree(), points.len());
        assert!(z.coeffs.last().unwrap().is_one());
        assert!(points.iter().all(|e| z.evaluate(e).is_zero()));
    }

    #[test]
    fn accepts_and_rejects_on_a_mostly_opened_domain() {
        // 100 transitions in a domain of 128, of which 90 are opened: this takes the
        // complement route in the prover
        let timer = Instant::now();
        let num_states = 10;
        let num_transitions = 100;
        let E_T: Vec<u64> = (90..100).collect();
        let E_step: Vec<u64> = (0..45).collect();
        let E_fairstep: Vec<u64> = (45..90).collect();
        let E_init: Vec<u64> = vec![7];
        let E_S0: Vec<u64> = vec![0, 1];
        let (proof, _) = prove(
            &E_init,
            &E_step,
            &E_fairstep,
            &E_S0,
            &E_T,
            num_states,
            num_transitions,
            &timer,
            3600,
        );
        let proof = proof.unwrap();
        assert_eq!(
            proof.verify(&E_init, &E_step, &E_fairstep, &timer, 3600),
            Some(true)
        );
        // 90 is in T, so claiming p_T opens to 0 there must fail
        assert_eq!(
            proof.verify(&E_init, &E_step, &vec![45u64, 90u64], &timer, 3600),
            Some(false)
        );
    }

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
