//! Multi-scalar multiplication for G1, backed by blstrs.
//!
//! zkmatrix's `dirac` accumulates linear combinations one scalar
//! multiplication at a time. On this curve that costs ~192us per term in
//! `bls12_381` (and ~767us per term when the accumulation happens in Gt),
//! which is what dominates both folding stages once the cross-term count
//! grows. blstrs exposes blst's Pippenger implementation at ~0.5us per
//! point, so we keep every public type in `bls12_381` -- the transcript,
//! the SRS and zkmatrix's protocols all speak it -- and drop into blstrs
//! purely as an arithmetic backend.
//!
//! The bridge is the uncompressed (96-byte) encoding, which both crates
//! implement identically and which costs ~0.2us per point, versus ~0.5us
//! for a single projective addition. Round-tripping is therefore cheaper
//! than doing the addition natively.

use bls12_381 as bls;
use group::{prime::PrimeCurveAffine, Curve, Group};
use rayon::prelude::*;
use zkmatrix::utils::curve::{G1Element, ZpElement};

/// Points below this count are accumulated directly; Pippenger only pays
/// off once the window setup is amortised.
const MSM_NAIVE_CUTOFF: usize = 8;

pub fn zp_to_blstrs(value: &ZpElement) -> blstrs::Scalar {
    Option::from(blstrs::Scalar::from_bytes_le(&value.value.to_bytes()))
        .expect("ZpElement is not a valid blstrs scalar")
}

pub fn g1_to_blstrs_affine(point: &bls::G1Affine) -> blstrs::G1Affine {
    Option::from(blstrs::G1Affine::from_uncompressed_unchecked(
        &point.to_uncompressed(),
    ))
    .expect("G1 point did not survive the uncompressed round trip")
}

pub fn blstrs_to_g1(point: &blstrs::G1Projective) -> G1Element {
    let affine = point.to_affine();
    let value: bls::G1Affine = Option::from(bls::G1Affine::from_uncompressed_unchecked(
        &affine.to_uncompressed(),
    ))
    .expect("G1 point did not survive the uncompressed round trip");
    G1Element {
        value: bls::G1Projective::from(value),
    }
}

/// Converts a batch of G1Elements to blstrs affine form, sharing one field
/// inversion across the batch.
pub fn to_blstrs_affine_batch(points: &[G1Element]) -> Vec<blstrs::G1Affine> {
    let projective: Vec<bls::G1Projective> = points.iter().map(|p| p.value).collect();
    let mut affine = vec![bls::G1Affine::identity(); projective.len()];
    bls::G1Projective::batch_normalize(&projective, &mut affine);
    affine.iter().map(g1_to_blstrs_affine).collect()
}

/// Window width for the fixed-base tables. Eight makes each window exactly
/// one byte of the little-endian scalar encoding, so digit extraction is an
/// array index.
const COMB_WINDOW_BITS: usize = 8;
const COMB_WINDOWS: usize = 32;

/// Fixed bases with precomputed small multiples, for commitments.
///
/// blst's `multi_exp` is the wrong tool for the 4|K| cross-term
/// commitments: below 32 points it degenerates to one full scalar
/// multiplication per point, it re-normalises the bases to affine (a field
/// inversion) on every call, and it spawns its own worker threads -- which
/// then contend with the rayon loop around it. A precomputed comb is
/// single-threaded, allocation-free per commitment, and measured 3-4x
/// faster in the shapes this protocol uses.
pub struct FixedBaseTable {
    tables: Vec<Vec<blstrs::G1Projective>>,
}

impl FixedBaseTable {
    pub fn new(points: &[G1Element]) -> Self {
        let affine = to_blstrs_affine_batch(points);
        let tables = affine
            .par_iter()
            .map(|base| {
                let base = blstrs::G1Projective::from(*base);
                let mut row = Vec::with_capacity(1 << COMB_WINDOW_BITS);
                let mut acc = blstrs::G1Projective::identity();
                for _ in 0..(1 << COMB_WINDOW_BITS) {
                    row.push(acc);
                    acc += base;
                }
                row
            })
            .collect();
        Self { tables }
    }

    pub fn len(&self) -> usize {
        self.tables.len()
    }

    /// sum_i scalars[i] * bases[i] over the first `scalars.len()` bases.
    ///
    /// Equivalent to `multi_exp` on the same operands; see the
    /// `comb_matches_msm` test.
    pub fn combine(&self, scalars: &[blstrs::Scalar]) -> blstrs::G1Projective {
        self.combine_with(scalars, &[])
    }

    /// As `combine`, plus `extra` scalars against the bases at `offsets`.
    pub fn combine_with(
        &self,
        scalars: &[blstrs::Scalar],
        extra: &[(usize, blstrs::Scalar)],
    ) -> blstrs::G1Projective {
        assert!(scalars.len() <= self.tables.len());
        let mut digits: Vec<(usize, [u8; 32])> = Vec::with_capacity(scalars.len() + extra.len());
        for (index, scalar) in scalars.iter().enumerate() {
            digits.push((index, scalar.to_bytes_le()));
        }
        for (index, scalar) in extra.iter() {
            assert!(*index < self.tables.len());
            digits.push((*index, scalar.to_bytes_le()));
        }

        let mut acc = blstrs::G1Projective::identity();
        let mut started = false;
        for window in (0..COMB_WINDOWS).rev() {
            if started {
                for _ in 0..COMB_WINDOW_BITS {
                    acc = acc.double();
                }
            }
            for (index, bytes) in digits.iter() {
                let digit = bytes[window] as usize;
                if digit != 0 {
                    acc += self.tables[*index][digit];
                    started = true;
                }
            }
        }
        acc
    }
}

/// Converts a batch of blstrs results back to G1Elements, sharing one field
/// inversion across the batch.
///
/// Converting one at a time costs an inversion each (`to_affine`), which at
/// 4|K| cross-term commitments is the whole cost of committing them.
pub fn from_blstrs_batch(points: &[blstrs::G1Projective]) -> Vec<G1Element> {
    let mut affine = vec![blstrs::G1Affine::identity(); points.len()];
    blstrs::G1Projective::batch_normalize(points, &mut affine);
    affine
        .par_iter()
        .map(|p| {
            let value: bls::G1Affine =
                Option::from(bls::G1Affine::from_uncompressed_unchecked(&p.to_uncompressed()))
                    .expect("G1 point did not survive the uncompressed round trip");
            G1Element {
                value: bls::G1Projective::from(value),
            }
        })
        .collect()
}

/// Compressed encodings of a batch of points, sharing one field inversion.
///
/// Used to absorb points into a Fiat-Shamir transcript: `TranElem::G1`
/// serialises by converting each point to affine individually, which is an
/// inversion per element.
pub fn compressed_batch(points: &[G1Element]) -> Vec<[u8; 48]> {
    let projective: Vec<bls::G1Projective> = points.iter().map(|p| p.value).collect();
    let mut affine = vec![bls::G1Affine::identity(); projective.len()];
    bls::G1Projective::batch_normalize(&projective, &mut affine);
    affine.iter().map(|p| p.to_compressed()).collect()
}

/// sum_i scalars[i] * points[i] for zkmatrix-typed operands.
///
/// Equivalent to folding with `+` and `*` term by term; see the
/// `msm_matches_naive` test.
pub fn msm_g1(points: &[G1Element], scalars: &[ZpElement]) -> G1Element {
    assert_eq!(points.len(), scalars.len());
    if points.is_empty() {
        return G1Element {
            value: bls::G1Projective::identity(),
        };
    }
    let blstrs_scalars: Vec<blstrs::Scalar> = scalars.par_iter().map(zp_to_blstrs).collect();
    let converted: Vec<blstrs::G1Projective> = to_blstrs_affine_batch(points)
        .into_iter()
        .map(blstrs::G1Projective::from)
        .collect();
    if converted.len() < MSM_NAIVE_CUTOFF {
        let mut acc = blstrs::G1Projective::identity();
        for (point, scalar) in converted.iter().zip(blstrs_scalars.iter()) {
            acc += *point * *scalar;
        }
        return blstrs_to_g1(&acc);
    }
    blstrs_to_g1(&blstrs::G1Projective::multi_exp(&converted, &blstrs_scalars))
}

/// Concatenation of two MSMs sharing one output, saving a conversion pass
/// over the (much larger) second operand list when both are needed.
pub fn msm_g1_pair(
    points_a: &[G1Element],
    scalars_a: &[ZpElement],
    points_b: &[G1Element],
    scalars_b: &[ZpElement],
) -> G1Element {
    let mut points = Vec::with_capacity(points_a.len() + points_b.len());
    points.extend_from_slice(points_a);
    points.extend_from_slice(points_b);
    let mut scalars = Vec::with_capacity(scalars_a.len() + scalars_b.len());
    scalars.extend_from_slice(scalars_a);
    scalars.extend_from_slice(scalars_b);
    msm_g1(&points, &scalars)
}
