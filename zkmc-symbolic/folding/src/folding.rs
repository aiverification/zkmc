//! One-shot folding of N proof obligations into a single instance.
//!
//! Column-vector commitments (lambda, mu, e, alpha, beta and all four
//! cross-term families) are carried in G1 form; see `commit.rs` for why that
//! is equivalent to the target-group commitment and what it saves. The
//! public matrices G and h are never committed at all: `zkmmeq` consumes
//! them in plaintext, as `fig:ZKMC-S2-fold` specifies.

use rayon::prelude::*;
use sha2::Digest;
use std::time::Instant;
use zkmatrix::mat::Mat;
use zkmatrix::setup::SRS;
use zkmatrix::utils::curve::{ConvertToZp, G1Element, G2Element, GtElement, ZpElement};
use zkmatrix::utils::fiat_shamir::{TranElem, TranSeq};

use crate::commit::*;
use crate::utils::curve_utils::*;
use crate::utils::msm::*;
use crate::utils::plain_utils::*;
use crate::utils::public_exponent_schedule::*;
use crate::utils::zk_utils::*;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/// Per-obligation commitments to a row-major family (A, -b), held in Gt.
#[derive(Clone)]
pub struct RowCommitments {
    pub randomness: Vec<ZpElement>,
    pub blind: Vec<GtElement>,
}

/// Per-obligation commitments to a column family (lambda, mu, e, alpha,
/// beta), held as the G1 preimage of the Gt commitment.
#[derive(Clone)]
pub struct ColCommitments {
    pub randomness: Vec<ZpElement>,
    pub blind: Vec<G1Element>,
}

impl ColCommitments {
    pub fn blind_gt(&self, srs: &SRS) -> Vec<GtElement> {
        self.blind
            .par_iter()
            .map(|p| col_comm_to_gt(*p, srs))
            .collect()
    }
}

/// Commitments to every per-obligation witness value.
#[derive(Clone)]
pub struct ObligationCommitments {
    pub a: RowCommitments,
    pub neg_b: RowCommitments,
    pub lambda: ColCommitments,
    pub mu: ColCommitments,
    pub e: ColCommitments,
    pub alpha: ColCommitments,
    pub beta: ColCommitments,
}

/// Aggregated cross-term commitments for one multiplication family.
#[derive(Clone)]
pub struct CrossTermCommitments {
    pub randomness: Vec<ZpElement>,
    pub blind: Vec<G1Element>,
}

/// A folded row-major operand, with everything zkmm needs.
pub struct FoldedRow {
    pub mat: Mat<ZpElement>,
    pub cache: Vec<G2Element>,
    pub randomness: ZpElement,
    pub blind: GtElement,
}

/// A folded column operand. `blind` is the G1 form; `blind_gt` pairs it up
/// for zkmm, which speaks Gt.
pub struct FoldedCol {
    pub mat: Mat<ZpElement>,
    pub cache: Vec<G1Element>,
    pub randomness: ZpElement,
    pub blind: G1Element,
}

impl FoldedCol {
    pub fn blind_gt(&self, srs: &SRS) -> GtElement {
        col_comm_to_gt(self.blind, srs)
    }
}

/// Equivalence classes of identical matrices among the obligations.
///
/// Used here only to reorganise arithmetic -- folding N repeats of the same
/// matrix as U scaled copies, and memoising the cross-term products. It does
/// not imply commitment sharing, which is a separate (and privacy-relevant)
/// decision made in `zkp.rs`.
pub struct DedupMap {
    pub class_of: Vec<usize>,
    pub class_count: usize,
}

impl DedupMap {
    pub fn build<T: std::hash::Hash + Eq + Clone>(items: &[T]) -> Self {
        let mut seen: std::collections::HashMap<&T, usize> = std::collections::HashMap::new();
        let mut class_of = Vec::with_capacity(items.len());
        for item in items.iter() {
            let next = seen.len();
            let class = *seen.entry(item).or_insert(next);
            class_of.push(class);
        }
        let class_count = seen.len();
        Self {
            class_of,
            class_count,
        }
    }

    /// One obligation index per class, the first one seen.
    pub fn representatives(&self) -> Vec<usize> {
        let mut reps = vec![usize::MAX; self.class_count];
        for (i, class) in self.class_of.iter().enumerate() {
            if reps[*class] == usize::MAX {
                reps[*class] = i;
            }
        }
        reps
    }

    /// Groups by equality alone, for types that are not hashable.
    ///
    /// Linear-scans a running list of distinct values, and gives up once
    /// that list grows past `limit` so an input with no repetition cannot
    /// degrade to a quadratic scan.
    pub fn build_by_eq<T: PartialEq>(items: &[T], limit: usize) -> Self {
        let mut distinct: Vec<&T> = Vec::new();
        let mut class_of = Vec::with_capacity(items.len());
        let mut class_count = 0usize;
        for item in items.iter() {
            let existing = if distinct.len() < limit {
                distinct.iter().position(|d| *d == item)
            } else {
                None
            };
            match existing {
                Some(class) => class_of.push(class),
                None => {
                    class_of.push(class_count);
                    if distinct.len() < limit {
                        distinct.push(item);
                    }
                    class_count += 1;
                }
            }
        }
        Self {
            class_of,
            class_count,
        }
    }

    /// sum over each class of the given per-obligation weights.
    pub fn class_weights(&self, weights: &[ZpElement]) -> Vec<ZpElement> {
        let mut sums = vec![ZpElement::from(0u64); self.class_count];
        for (i, class) in self.class_of.iter().enumerate() {
            sums[*class] += weights[i];
        }
        sums
    }
}

/// Everything the prover produces while folding.
pub struct ProverFold {
    pub cross_a: CrossTermCommitments,
    pub cross_b: CrossTermCommitments,
    pub cross_g: CrossTermCommitments,
    pub cross_h: CrossTermCommitments,
    pub a_star: FoldedRow,
    pub b_star: FoldedRow,
    pub g_star: Mat<ZpElement>,
    pub h_star: Mat<ZpElement>,
    pub lambda_star: FoldedCol,
    pub mu_star: FoldedCol,
    pub e_a_star: FoldedCol,
    pub e_g_star: FoldedCol,
    pub alpha_star: FoldedCol,
    pub beta_star: FoldedCol,
    pub challenge: ZpElement,
}

/// Everything the verifier recomputes while folding.
pub struct VerifierFold {
    pub a_star_blind: GtElement,
    pub b_star_blind: GtElement,
    pub lambda_star_blind: G1Element,
    pub mu_star_blind: G1Element,
    pub e_a_star_blind: G1Element,
    pub e_g_star_blind: G1Element,
    pub alpha_star_blind: G1Element,
    pub beta_star_blind: G1Element,
    pub g_star: Mat<ZpElement>,
    pub h_star: Mat<ZpElement>,
    pub challenge: ZpElement,
}

// ---------------------------------------------------------------------------
// Commitment
// ---------------------------------------------------------------------------

/// Commits to every per-obligation witness value.
///
/// G_i and h_i are absent by design: they are public, and `zkmmeq` takes
/// them in the clear.
pub fn commit_obligations(
    a_i: &[Vec<Vec<i64>>],
    neg_b_i: &[Vec<Vec<i64>>],
    lambda_i: &[Vec<Vec<i64>>],
    mu_i: &[Vec<Vec<i64>>],
    e_i: &[Vec<Vec<i64>>],
    alpha_i: &[Vec<Vec<i64>>],
    beta_i: &[Vec<Vec<i64>>],
    srs: &SRS,
    committer: &ColCommitter,
) -> ObligationCommitments {
    let n = a_i.len();
    assert!(neg_b_i.len() == n && lambda_i.len() == n && mu_i.len() == n);
    assert!(e_i.len() == n && alpha_i.len() == n && beta_i.len() == n);

    ObligationCommitments {
        a: commit_row_family("A", a_i, srs),
        neg_b: commit_row_family("-b", neg_b_i, srs),
        lambda: commit_col_family(lambda_i, committer),
        mu: commit_col_family(mu_i, committer),
        e: commit_col_family(e_i, committer),
        alpha: commit_col_family(alpha_i, committer),
        beta: commit_col_family(beta_i, committer),
    }
}

/// Commits one row-major family, computing one commitment per *distinct*
/// matrix and expanding it across the obligations that share it.
///
/// This reproduces exactly the sharing the unfolded prototype already did --
/// duplicates carry the same commitment and the same randomness -- but at U
/// commitments instead of N. Note that sharing a commitment also reveals
/// which obligations carry the same secret matrix; that trade-off is
/// unchanged here, not introduced by it.
pub fn commit_row_family_deduped(
    name: &str,
    unique: &[Vec<Vec<i64>>],
    class_of: &[usize],
    srs: &SRS,
) -> RowCommitments {
    let unique_randomness: Vec<ZpElement> = (0..unique.len()).map(|_| ZpElement::rand()).collect();
    let unique_blind: Vec<GtElement> = unique
        .par_iter()
        .zip(unique_randomness.par_iter())
        .map(|(mat, r)| {
            let converted = vec_mat_to_zkmatrix_i64(name.to_string(), mat);
            let (commitment, _) = commit_rm_i64(&converted, srs);
            commitment + (*r * srs.blind_base)
        })
        .collect();

    RowCommitments {
        randomness: class_of.iter().map(|c| unique_randomness[*c]).collect(),
        blind: class_of.iter().map(|c| unique_blind[*c]).collect(),
    }
}

pub fn commit_row_family(name: &str, mats: &[Vec<Vec<i64>>], srs: &SRS) -> RowCommitments {
    let randomness: Vec<ZpElement> = (0..mats.len()).map(|_| ZpElement::rand()).collect();
    let blind = mats
        .par_iter()
        .zip(randomness.par_iter())
        .map(|(mat, r)| {
            let converted = vec_mat_to_zkmatrix_i64(name.to_string(), mat);
            let (commitment, _) = commit_rm_i64(&converted, srs);
            commitment + (*r * srs.blind_base)
        })
        .collect();
    RowCommitments { randomness, blind }
}

pub fn commit_col_family(mats: &[Vec<Vec<i64>>], committer: &ColCommitter) -> ColCommitments {
    let randomness: Vec<ZpElement> = (0..mats.len()).map(|_| ZpElement::rand()).collect();
    let blind = mats
        .par_iter()
        .zip(randomness.par_iter())
        .map(|(mat, r)| {
            let values: Vec<ZpElement> = mat.iter().map(|row| ZpElement::from(0u64) + i64_to_zp(row[0])).collect();
            committer.commit(&values, *r)
        })
        .collect();
    ColCommitments { randomness, blind }
}

fn i64_to_zp(value: i64) -> ZpElement {
    value.to_zp()
}

// ---------------------------------------------------------------------------
// Cross terms
// ---------------------------------------------------------------------------

/// Above this many memoised products we stop caching and accumulate
/// directly, so that inputs with no repetition cannot blow up memory.
const MEMO_PRODUCT_LIMIT: usize = 1 << 20;

/// Cap on the distinct-commitment scan the verifier does before folding
/// the row-major families; beyond this the scan is not worth its own cost.
const GT_DEDUP_LIMIT: usize = 256;

/// Aggregated cross terms C_k = sum_{c_i + c_j = k, i != j} M_i v_j.
///
/// The products are accumulated in `i128` and reduced mod p once at the end:
/// the operands are bounded integers, so the field arithmetic in the inner
/// loop (a multiplication plus a `to_zp` conversion per non-zero) is pure
/// overhead. `overflow_guard` pins the bound that makes this safe.
///
/// Where the same matrix or the same vector recurs across obligations -- and
/// in practice a task has a handful of distinct A and G matrices spread over
/// thousands of obligations -- each distinct product is computed once and the
/// buckets are then built by vector addition.
pub fn compute_cross_terms(
    mats: &[Mat<i64>],
    vectors: &[Vec<i64>],
    pairs: &CrossTermPairs,
    out_len: usize,
    mat_classes: &DedupMap,
    vec_classes: &DedupMap,
) -> Vec<Vec<ZpElement>> {
    overflow_guard(mats, vectors, pairs);

    let memo_size = mat_classes
        .class_count
        .saturating_mul(vec_classes.class_count);
    if memo_size <= MEMO_PRODUCT_LIMIT {
        cross_terms_memoised(mats, vectors, pairs, out_len, mat_classes, vec_classes)
    } else {
        cross_terms_direct(mats, vectors, pairs, out_len)
    }
}

/// Panics unless every bucket sum provably fits in an `i128`.
///
/// A bucket is at most `pairs` products, each a sum of `cols` terms bounded
/// by max|matrix entry| * max|vector entry|.
fn overflow_guard(mats: &[Mat<i64>], vectors: &[Vec<i64>], pairs: &CrossTermPairs) {
    let max_mat = mats
        .par_iter()
        .map(|m| m.data.iter().map(|(_, _, v)| v.unsigned_abs() as u128).max().unwrap_or(0))
        .max()
        .unwrap_or(0);
    let max_vec = vectors
        .par_iter()
        .map(|v| v.iter().map(|x| x.unsigned_abs() as u128).max().unwrap_or(0))
        .max()
        .unwrap_or(0);
    let cols = mats.first().map(|m| m.shape.1 as u128).unwrap_or(0);
    let widest_bucket = pairs.pairs.iter().map(|g| g.len()).max().unwrap_or(0) as u128;

    let bound = max_mat
        .checked_mul(max_vec)
        .and_then(|x| x.checked_mul(cols))
        .and_then(|x| x.checked_mul(widest_bucket));

    match bound {
        Some(value) if value < (i128::MAX as u128) => {}
        _ => panic!(
            "cross-term accumulation would overflow i128 \
             (max entries {max_mat} x {max_vec}, {cols} columns, widest bucket {widest_bucket})"
        ),
    }
}

fn cross_terms_direct(
    mats: &[Mat<i64>],
    vectors: &[Vec<i64>],
    pairs: &CrossTermPairs,
    out_len: usize,
) -> Vec<Vec<ZpElement>> {
    pairs
        .pairs
        .par_iter()
        .map(|group| {
            let mut acc = vec![0i128; out_len];
            for &(i, j) in group.iter() {
                accumulate_product(&mats[i], &vectors[j], &mut acc);
            }
            reduce_i128(&acc)
        })
        .collect()
}

fn cross_terms_memoised(
    mats: &[Mat<i64>],
    vectors: &[Vec<i64>],
    pairs: &CrossTermPairs,
    out_len: usize,
    mat_classes: &DedupMap,
    vec_classes: &DedupMap,
) -> Vec<Vec<ZpElement>> {
    let n_vec_classes = vec_classes.class_count;
    let slot = |u: usize, v: usize| u * n_vec_classes + v;

    // Which distinct (matrix class, vector class) products actually occur.
    let mut needed = vec![false; mat_classes.class_count * n_vec_classes];
    for group in pairs.pairs.iter() {
        for &(i, j) in group.iter() {
            needed[slot(mat_classes.class_of[i], vec_classes.class_of[j])] = true;
        }
    }

    let mat_reps = mat_classes.representatives();
    let vec_reps = vec_classes.representatives();
    let products: Vec<Option<Vec<i128>>> = needed
        .par_iter()
        .enumerate()
        .map(|(index, wanted)| {
            if !wanted {
                return None;
            }
            let u = index / n_vec_classes;
            let v = index % n_vec_classes;
            let mut acc = vec![0i128; out_len];
            accumulate_product(&mats[mat_reps[u]], &vectors[vec_reps[v]], &mut acc);
            Some(acc)
        })
        .collect();

    pairs
        .pairs
        .par_iter()
        .map(|group| {
            let mut acc = vec![0i128; out_len];
            for &(i, j) in group.iter() {
                let product = products[slot(mat_classes.class_of[i], vec_classes.class_of[j])]
                    .as_ref()
                    .expect("memoised cross-term product missing");
                for (slot, value) in acc.iter_mut().zip(product.iter()) {
                    *slot += *value;
                }
            }
            reduce_i128(&acc)
        })
        .collect()
}

fn accumulate_product(mat: &Mat<i64>, vector: &[i64], acc: &mut [i128]) {
    for &(row, col, value) in mat.data.iter() {
        if value != 0 {
            acc[row] += (value as i128) * (vector[col] as i128);
        }
    }
}

fn reduce_i128(values: &[i128]) -> Vec<ZpElement> {
    values
        .iter()
        .map(|v| {
            if *v >= 0 {
                u128_to_zp(*v as u128)
            } else {
                ZpElement::from(0u64) - u128_to_zp(v.unsigned_abs())
            }
        })
        .collect()
}

fn u128_to_zp(value: u128) -> ZpElement {
    let low = (value & u64::MAX as u128) as u64;
    let high = (value >> 64) as u64;
    let two_64 = ZpElement::from(u64::MAX) + ZpElement::from(1u64);
    ZpElement::from(high) * two_64 + ZpElement::from(low)
}

// ---------------------------------------------------------------------------
// Prover
// ---------------------------------------------------------------------------

pub struct FoldContext<'a> {
    pub srs: &'a SRS,
    pub schedule: &'a [u128],
    pub pairs: &'a CrossTermPairs,
    pub committer: &'a ColCommitter,
}

pub fn prover_fold(
    a_i: &[Vec<Vec<i64>>],
    neg_b_i: &[Vec<Vec<i64>>],
    neg_g_i: &[Vec<Vec<i64>>],
    neg_h_i: &[Vec<Vec<i64>>],
    lambda_i: &[Vec<Vec<i64>>],
    mu_i: &[Vec<Vec<i64>>],
    e_i: &[Vec<Vec<i64>>],
    alpha_i: &[Vec<Vec<i64>>],
    beta_i: &[Vec<Vec<i64>>],
    commitments: &ObligationCommitments,
    ctx: &FoldContext,
) -> ProverFold {
    let srs = ctx.srs;
    let n = a_i.len();
    assert!(n >= 2);
    debug_assert_eq!(ctx.schedule.len(), n);
    debug_assert!(schedule_has_no_2c_collisions(ctx.schedule));
    let k = ctx.pairs.len();

    let conversion_timer = Instant::now();
    let a_mat = to_mats("A", a_i);
    let neg_b_mat = to_mats("-b", neg_b_i);
    let neg_g_mat = to_mats("-G", neg_g_i);
    let neg_h_mat = to_mats("-h", neg_h_i);
    let lambda_mat = to_mats("lambda", lambda_i);
    let mu_mat = to_mats("mu", mu_i);
    let e_mat = to_mats("e", e_i);
    let alpha_mat = to_mats("alpha", alpha_i);
    let beta_mat = to_mats("beta", beta_i);
    let lambda_flat = to_flat_columns(lambda_i);
    let mu_flat = to_flat_columns(mu_i);
    let conversion_time = conversion_timer.elapsed().as_micros();

    let dedupe_timer = Instant::now();
    let a_classes = DedupMap::build(a_i);
    let b_classes = DedupMap::build(neg_b_i);
    let g_classes = DedupMap::build(neg_g_i);
    let h_classes = DedupMap::build(neg_h_i);
    let lambda_classes = DedupMap::build(&lambda_flat);
    let mu_classes = DedupMap::build(&mu_flat);
    let dedupe_time = dedupe_timer.elapsed().as_micros();

    let out_len_e = e_i[0].len();
    let out_len_scalar = alpha_i[0].len();

    let cross_timer = Instant::now();
    let (cross_a_values, cross_b_values, cross_g_values, cross_h_values) = {
        let mut results: Vec<Vec<Vec<ZpElement>>> = vec![Vec::new(); 4];
        results
            .par_iter_mut()
            .enumerate()
            .for_each(|(index, slot)| {
                *slot = match index {
                    0 => compute_cross_terms(
                        &a_mat, &lambda_flat, ctx.pairs, out_len_e, &a_classes, &lambda_classes,
                    ),
                    1 => compute_cross_terms(
                        &neg_b_mat, &lambda_flat, ctx.pairs, out_len_scalar, &b_classes,
                        &lambda_classes,
                    ),
                    2 => compute_cross_terms(
                        &neg_g_mat, &mu_flat, ctx.pairs, out_len_e, &g_classes, &mu_classes,
                    ),
                    _ => compute_cross_terms(
                        &neg_h_mat, &mu_flat, ctx.pairs, out_len_scalar, &h_classes, &mu_classes,
                    ),
                };
            });
        let mut drain = results.into_iter();
        (
            drain.next().unwrap(),
            drain.next().unwrap(),
            drain.next().unwrap(),
            drain.next().unwrap(),
        )
    };
    let cross_time = cross_timer.elapsed().as_micros();

    let cross_commit_timer = Instant::now();
    let cross_a = commit_cross_terms(&cross_a_values, ctx.committer, k);
    let cross_b = commit_cross_terms(&cross_b_values, ctx.committer, k);
    let cross_g = commit_cross_terms(&cross_g_values, ctx.committer, k);
    let cross_h = commit_cross_terms(&cross_h_values, ctx.committer, k);
    let cross_commit_time = cross_commit_timer.elapsed().as_micros();

    let challenge_timer = Instant::now();
    let mut transcript = TranSeq::new();
    push_transcript(&mut transcript, commitments, &cross_a, &cross_b, &cross_g, &cross_h);
    let r = transcript.gen_challenge();
    let powers = ChallengePowers::new(r, ctx.schedule, ctx.pairs);
    let challenge_time = challenge_timer.elapsed().as_micros();

    let fold_timer = Instant::now();
    let a_star_mat = fold_by_class("A*", &a_mat, &a_classes, &powers.schedule);
    let b_star_mat = fold_by_class("b*", &neg_b_mat, &b_classes, &powers.schedule);
    let g_star_mat = fold_by_class("G*", &neg_g_mat, &g_classes, &powers.schedule);
    let h_star_mat = fold_by_class("h*", &neg_h_mat, &h_classes, &powers.schedule);
    let lambda_star_mat = fold_by_class("lambda*", &lambda_mat, &lambda_classes, &powers.schedule);
    let mu_star_mat = fold_by_class("mu*", &mu_mat, &mu_classes, &powers.schedule);

    // e_A* and e_G* share the diagonal sum; only the cross terms differ.
    let e_diag = fold_columns(&e_mat, &powers.diagonal);
    let e_a_values = accumulate_weighted_columns(e_diag.clone(), &cross_a_values, &powers.cross);
    let e_g_values = accumulate_weighted_columns(e_diag, &cross_g_values, &powers.cross);
    let alpha_values = accumulate_weighted_columns(
        fold_columns(&alpha_mat, &powers.diagonal),
        &cross_b_values,
        &powers.cross,
    );
    let beta_values = accumulate_weighted_columns(
        fold_columns(&beta_mat, &powers.diagonal),
        &cross_h_values,
        &powers.cross,
    );
    let fold_time = fold_timer.elapsed().as_micros();

    let cache_timer = Instant::now();
    let (a_star_comm, a_star_cache) = commit_rm_zp(&a_star_mat, srs);
    let (b_star_comm, b_star_cache) = commit_rm_zp(&b_star_mat, srs);
    let a_star_randomness = weighted_zp_sum(&commitments.a.randomness, &powers.schedule);
    let b_star_randomness = weighted_zp_sum(&commitments.neg_b.randomness, &powers.schedule);

    let lambda_star = folded_col_from_values(
        "lambda*",
        &mat_col_to_dense_zp(&lambda_star_mat),
        weighted_zp_sum(&commitments.lambda.randomness, &powers.schedule),
        ctx.committer,
        srs,
    );
    let mu_star = folded_col_from_values(
        "mu*",
        &mat_col_to_dense_zp(&mu_star_mat),
        weighted_zp_sum(&commitments.mu.randomness, &powers.schedule),
        ctx.committer,
        srs,
    );
    let e_a_star = folded_col_from_values(
        "e_A*",
        &e_a_values,
        weighted_zp_sum(&commitments.e.randomness, &powers.diagonal)
            + weighted_zp_sum(&cross_a.randomness, &powers.cross),
        ctx.committer,
        srs,
    );
    let e_g_star = folded_col_from_values(
        "e_G*",
        &e_g_values,
        weighted_zp_sum(&commitments.e.randomness, &powers.diagonal)
            + weighted_zp_sum(&cross_g.randomness, &powers.cross),
        ctx.committer,
        srs,
    );
    let alpha_star = folded_col_from_values(
        "alpha*",
        &alpha_values,
        weighted_zp_sum(&commitments.alpha.randomness, &powers.diagonal)
            + weighted_zp_sum(&cross_b.randomness, &powers.cross),
        ctx.committer,
        srs,
    );
    let beta_star = folded_col_from_values(
        "beta*",
        &beta_values,
        weighted_zp_sum(&commitments.beta.randomness, &powers.diagonal)
            + weighted_zp_sum(&cross_h.randomness, &powers.cross),
        ctx.committer,
        srs,
    );
    let cache_time = cache_timer.elapsed().as_micros();

    let total = conversion_time
        + dedupe_time
        + cross_time
        + cross_commit_time
        + challenge_time
        + fold_time
        + cache_time;
    println!();
    print_timing("Prover Fold Timing:", total);
    print_timing("--Input conversion time:", conversion_time);
    print_timing("--Dedup classes time:", dedupe_time);
    print_timing("--Cross terms arithmetic time:", cross_time);
    print_timing("--Cross terms commitment time:", cross_commit_time);
    print_timing("--Challenge + powers time:", challenge_time);
    print_timing("--Star matrices folding time:", fold_time);
    print_timing("--Star caches + blinds time:", cache_time);
    println!();

    ProverFold {
        cross_a,
        cross_b,
        cross_g,
        cross_h,
        a_star: FoldedRow {
            mat: a_star_mat,
            cache: a_star_cache,
            randomness: a_star_randomness,
            blind: a_star_comm + (a_star_randomness * srs.blind_base),
        },
        b_star: FoldedRow {
            mat: b_star_mat,
            cache: b_star_cache,
            randomness: b_star_randomness,
            blind: b_star_comm + (b_star_randomness * srs.blind_base),
        },
        g_star: g_star_mat,
        h_star: h_star_mat,
        lambda_star,
        mu_star,
        e_a_star,
        e_g_star,
        alpha_star,
        beta_star,
        challenge: r,
    }
}

fn folded_col_from_values(
    id: &str,
    values: &[ZpElement],
    randomness: ZpElement,
    committer: &ColCommitter,
    srs: &SRS,
) -> FoldedCol {
    let mat = dense_zp_to_mat_col(id, &values.to_vec());
    let (_, cache) = commit_cm_zp(&mat, srs);
    FoldedCol {
        mat,
        cache,
        randomness,
        blind: committer.commit(values, randomness),
    }
}

fn commit_cross_terms(
    values: &[Vec<ZpElement>],
    committer: &ColCommitter,
    k: usize,
) -> CrossTermCommitments {
    assert_eq!(values.len(), k);
    let randomness: Vec<ZpElement> = (0..k).map(|_| ZpElement::rand()).collect();
    let blind = committer.commit_many(values, &randomness);
    CrossTermCommitments { randomness, blind }
}

// ---------------------------------------------------------------------------
// Verifier
// ---------------------------------------------------------------------------

pub fn verifier_fold(
    commitments: &ObligationCommitments,
    cross_a: &CrossTermCommitments,
    cross_b: &CrossTermCommitments,
    cross_g: &CrossTermCommitments,
    cross_h: &CrossTermCommitments,
    neg_g_i: &[Vec<Vec<i64>>],
    neg_h_i: &[Vec<Vec<i64>>],
    ctx: &FoldContext,
) -> VerifierFold {
    let n = commitments.a.blind.len();
    debug_assert_eq!(ctx.schedule.len(), n);
    debug_assert_eq!(cross_a.blind.len(), ctx.pairs.len());
    debug_assert!(schedule_has_no_2c_collisions(ctx.schedule));

    let challenge_timer = Instant::now();
    let mut transcript = TranSeq::new();
    push_transcript(&mut transcript, commitments, cross_a, cross_b, cross_g, cross_h);
    let r = transcript.gen_challenge();
    let powers = ChallengePowers::new(r, ctx.schedule, ctx.pairs);
    let challenge_time = challenge_timer.elapsed().as_micros();

    // A and -b are row-major, so their commitments stay in Gt. Obligations
    // sharing a matrix share a commitment, so we scale each distinct one
    // once instead of N times.
    let row_timer = Instant::now();
    let a_classes = DedupMap::build_by_eq(&commitments.a.blind, GT_DEDUP_LIMIT);
    let b_classes = DedupMap::build_by_eq(&commitments.neg_b.blind, GT_DEDUP_LIMIT);
    let a_star_blind = weighted_gt_by_class(&commitments.a.blind, &a_classes, &powers.schedule);
    let b_star_blind = weighted_gt_by_class(&commitments.neg_b.blind, &b_classes, &powers.schedule);
    let row_time = row_timer.elapsed().as_micros();

    // Everything else folds as a G1 multi-scalar multiplication.
    let msm_timer = Instant::now();
    let lambda_star_blind = msm_g1(&commitments.lambda.blind, &powers.schedule);
    let mu_star_blind = msm_g1(&commitments.mu.blind, &powers.schedule);
    let e_a_star_blind = msm_g1_pair(
        &commitments.e.blind,
        &powers.diagonal,
        &cross_a.blind,
        &powers.cross,
    );
    let e_g_star_blind = msm_g1_pair(
        &commitments.e.blind,
        &powers.diagonal,
        &cross_g.blind,
        &powers.cross,
    );
    let alpha_star_blind = msm_g1_pair(
        &commitments.alpha.blind,
        &powers.diagonal,
        &cross_b.blind,
        &powers.cross,
    );
    let beta_star_blind = msm_g1_pair(
        &commitments.beta.blind,
        &powers.diagonal,
        &cross_h.blind,
        &powers.cross,
    );
    let msm_time = msm_timer.elapsed().as_micros();

    // G* and h* are public and are only ever used in plaintext, by zkmmeq.
    let public_timer = Instant::now();
    let neg_g_mat = to_mats("-G", neg_g_i);
    let neg_h_mat = to_mats("-h", neg_h_i);
    let g_classes = DedupMap::build(neg_g_i);
    let h_classes = DedupMap::build(neg_h_i);
    let g_star = fold_by_class("G*", &neg_g_mat, &g_classes, &powers.schedule);
    let h_star = fold_by_class("h*", &neg_h_mat, &h_classes, &powers.schedule);
    let public_time = public_timer.elapsed().as_micros();

    let total = challenge_time + row_time + msm_time + public_time;
    println!();
    print_timing("Verifier Fold Timing:", total);
    print_timing("--Challenge + powers time:", challenge_time);
    print_timing("--A*, b* (Gt) blind time:", row_time);
    print_timing("--lambda*, mu*, e_A*, e_G*, alpha*, beta* (G1 MSM) time:", msm_time);
    print_timing("--G*, h* plaintext fold time:", public_time);
    println!();

    VerifierFold {
        a_star_blind,
        b_star_blind,
        lambda_star_blind,
        mu_star_blind,
        e_a_star_blind,
        e_g_star_blind,
        alpha_star_blind,
        beta_star_blind,
        g_star,
        h_star,
        challenge: r,
    }
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/// Powers of the challenge used by both sides.
///
/// Cross-term powers are formed as r^{c_i} * r^{c_j} from a representative
/// pair of each bucket, so no exponentiation is needed for them.
pub struct ChallengePowers {
    pub schedule: Vec<ZpElement>,
    pub diagonal: Vec<ZpElement>,
    pub cross: Vec<ZpElement>,
}

impl ChallengePowers {
    pub fn new(r: ZpElement, schedule: &[u128], pairs: &CrossTermPairs) -> Self {
        let schedule_powers: Vec<ZpElement> = schedule
            .par_iter()
            .map(|c| r.pow(*c as u64))
            .collect();
        let diagonal = schedule_powers.par_iter().map(|p| *p * *p).collect();
        let cross = pairs
            .representatives()
            .par_iter()
            .map(|(i, j)| schedule_powers[*i] * schedule_powers[*j])
            .collect();
        Self {
            schedule: schedule_powers,
            diagonal,
            cross,
        }
    }
}

/// Absorbs the whole commitment set into the Fiat-Shamir transcript.
///
/// The G1 families go in as batched compressed encodings rather than through
/// `TranElem::G1`, whose `Serialize` impl converts each point to affine
/// individually -- a field inversion per element, which with 4|K| cross-term
/// commitments cost more than everything else in the fold combined. Batch
/// normalisation shares one inversion across the set; the bytes absorbed are
/// the same canonical compressed encoding, and the ordering is fixed by the
/// public parameters, so both parties absorb exactly the same string.
fn push_transcript(
    transcript: &mut TranSeq,
    commitments: &ObligationCommitments,
    cross_a: &CrossTermCommitments,
    cross_b: &CrossTermCommitments,
    cross_g: &CrossTermCommitments,
    cross_h: &CrossTermCommitments,
) {
    let n = commitments.a.blind.len();
    let k = cross_a.blind.len();

    transcript.hasher.update(b"zkmc-fold-v1");
    transcript.hasher.update((n as u64).to_le_bytes());
    transcript.hasher.update((k as u64).to_le_bytes());

    // Row-major families stay in Gt, where serialisation is just a field
    // repacking, and there are only 2N of them.
    for i in 0..n {
        transcript.push(TranElem::Gt(commitments.a.blind[i]));
        transcript.push(TranElem::Gt(commitments.neg_b.blind[i]));
    }
    for family in [
        &commitments.lambda.blind,
        &commitments.mu.blind,
        &commitments.e.blind,
        &commitments.alpha.blind,
        &commitments.beta.blind,
        &cross_a.blind,
        &cross_b.blind,
        &cross_g.blind,
        &cross_h.blind,
    ] {
        for bytes in compressed_batch(family) {
            transcript.hasher.update(bytes);
        }
    }
}

fn to_mats(name: &str, mats: &[Vec<Vec<i64>>]) -> Vec<Mat<i64>> {
    mats.par_iter()
        .map(|m| vec_mat_to_zkmatrix_i64(name.to_string(), m))
        .collect()
}

fn to_flat_columns(mats: &[Vec<Vec<i64>>]) -> Vec<Vec<i64>> {
    mats.par_iter()
        .map(|m| m.iter().map(|row| row[0]).collect())
        .collect()
}

/// sum_i powers[i] * mats[i], grouping obligations that share a matrix so
/// each distinct one is scaled once.
fn fold_by_class(
    id: &str,
    mats: &[Mat<i64>],
    classes: &DedupMap,
    powers: &[ZpElement],
) -> Mat<ZpElement> {
    let reps = classes.representatives();
    let weights = classes.class_weights(powers);
    let unique: Vec<&Mat<i64>> = reps.iter().map(|i| &mats[*i]).collect();
    let shape = unique[0].shape;
    let len = shape.0 * shape.1;

    let dense = unique
        .par_iter()
        .zip(weights.par_iter())
        .fold(
            || vec![ZpElement::from(0u64); len],
            |mut acc, (mat, weight)| {
                for (row, col, value) in mat.data.iter() {
                    if *value != 0 {
                        acc[row * shape.1 + col] += value.to_zp() * *weight;
                    }
                }
                acc
            },
        )
        .reduce(
            || vec![ZpElement::from(0u64); len],
            |mut a, b| {
                for (x, y) in a.iter_mut().zip(b.iter()) {
                    *x += *y;
                }
                a
            },
        );

    dense_to_mat(id, &dense, shape)
}

fn fold_columns(mats: &[Mat<i64>], powers: &[ZpElement]) -> Vec<ZpElement> {
    let len = mats[0].shape.0;
    mats.par_iter()
        .zip(powers.par_iter())
        .fold(
            || vec![ZpElement::from(0u64); len],
            |mut acc, (mat, power)| {
                for (row, _, value) in mat.data.iter() {
                    acc[*row] += value.to_zp() * *power;
                }
                acc
            },
        )
        .reduce(
            || vec![ZpElement::from(0u64); len],
            |mut a, b| {
                for (x, y) in a.iter_mut().zip(b.iter()) {
                    *x += *y;
                }
                a
            },
        )
}

fn weighted_gt_by_class(
    blinds: &[GtElement],
    classes: &DedupMap,
    powers: &[ZpElement],
) -> GtElement {
    let reps = classes.representatives();
    let weights = classes.class_weights(powers);
    reps.par_iter()
        .zip(weights.par_iter())
        .map(|(i, weight)| blinds[*i] * *weight)
        .reduce(get_bls_gt_zero, |a, b| a + b)
}

/// sum_i a[i] * b[i] over Zp.
pub fn weighted_zp_sum(a: &[ZpElement], b: &[ZpElement]) -> ZpElement {
    a.par_iter()
        .zip(b.par_iter())
        .map(|(x, y)| *x * *y)
        .reduce(|| ZpElement::from(0u64), |acc, value| acc + value)
}

pub fn print_timing(label: &str, time_us: u128) {
    let time_ms = time_us / 1_000 + u128::from(time_us % 1_000 >= 500);
    println!("{label:<70}{time_us:>15}us ({time_ms:>8}ms)");
}
