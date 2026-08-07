use bls_bulletproofs::{BulletproofGens, PedersenGens, ProofError, RangeProof};
use blstrs;
use merlin::Transcript;

#[derive(Debug, Clone)]
pub enum RangeProofError {
    ProofError(ProofError),
    //Used when a user tries to verify a proof that has no commitments
    NoCommitments,
    //Used when a user tries to verify a proof that has too many commitments
    TooManyCommitments,
    //Used when a user tries to verify a proof that has too few commitments
    TooFewCommitments,
    //Used when number of commitments do not match
    MismatchingCommitmentLengths,
    //Used when bound b is invalid
    InvalidBound,
    //Used when provided n does not match proof's n
    MismatchingParameters,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ArbitraryUpperRangeProof {
    pub lower_proof: RangeProof,
    pub upper_proof: RangeProof,
    pub n: usize,
    pub b: u32,
}

impl ArbitraryUpperRangeProof {
    pub fn prove_single(
        pc_gens: &PedersenGens,
        bp_gens: &BulletproofGens,
        transcript: &mut Transcript,
        val: i64,
        b: u32,
        lower_blinding: &blstrs::Scalar,
        upper_blinding: &blstrs::Scalar,
        n: usize,
    ) -> Result<ArbitraryUpperRangeProof, RangeProofError> {
        if b == 0 {
            return Err(RangeProofError::InvalidBound);
        }
        let b_min_val = ((b as i64) - val) as u64;
        let lower_proof_result =
            RangeProof::prove_single(bp_gens, pc_gens, transcript, val as u64, lower_blinding, n);
        if lower_proof_result.is_err() {
            return Err(RangeProofError::ProofError(lower_proof_result.unwrap_err()));
        }
        let (lower_proof, _lower_comm) = lower_proof_result.unwrap();
        let upper_proof_result =
            RangeProof::prove_single(bp_gens, pc_gens, transcript, b_min_val, upper_blinding, n);
        if upper_proof_result.is_err() {
            return Err(RangeProofError::ProofError(upper_proof_result.unwrap_err()));
        }
        let (upper_proof, _upper_comm) = upper_proof_result.unwrap();
        return Ok(ArbitraryUpperRangeProof {
            lower_proof,
            upper_proof,
            n,
            b,
        });
    }

    pub fn prove_multiple(
        pc_gens: &PedersenGens,
        bp_gens: &BulletproofGens,
        transcript: &mut Transcript,
        vals: &[i64],
        b: u32,
        lower_blindings: &[blstrs::Scalar],
        upper_blindings: &[blstrs::Scalar],
        n: usize,
    ) -> Result<ArbitraryUpperRangeProof, RangeProofError> {
        if b == 0 {
            return Err(RangeProofError::InvalidBound);
        }
        let vals_u64: Vec<u64> = vals.iter().map(|v| *v as u64).collect();
        let mut b_min_vals = vec![];
        for val in vals.iter() {
            b_min_vals.push(((b as i64) - val) as u64);
        }
        let lower_proof_result =
            RangeProof::prove_multiple(bp_gens, pc_gens, transcript, &vals_u64, lower_blindings, n);
        if lower_proof_result.is_err() {
            return Err(RangeProofError::ProofError(lower_proof_result.unwrap_err()));
        }
        let (lower_proof, _lower_comms) = lower_proof_result.unwrap();
        let upper_proof_result = RangeProof::prove_multiple(
            bp_gens,
            pc_gens,
            transcript,
            &b_min_vals,
            upper_blindings,
            n,
        );
        if upper_proof_result.is_err() {
            return Err(RangeProofError::ProofError(upper_proof_result.unwrap_err()));
        }
        let (upper_proof, _upper_comms) = upper_proof_result.unwrap();
        return Ok(ArbitraryUpperRangeProof {
            lower_proof,
            upper_proof,
            n,
            b,
        });
    }

    pub fn verify_single(
        &self,
        pc_gens: &PedersenGens,
        bp_gens: &BulletproofGens,
        transcript: &mut Transcript,
        lower_comm: &blstrs::G1Affine,
        upper_comm: &blstrs::G1Affine,
        n: usize,
    ) -> Result<bool, RangeProofError> {
        if n != self.n {
            return Err(RangeProofError::MismatchingParameters);
        }
        let lower_verif_result =
            self.lower_proof
                .verify_single(bp_gens, pc_gens, transcript, lower_comm, n);
        if lower_verif_result.is_err() {
            return Err(RangeProofError::ProofError(lower_verif_result.unwrap_err()));
        }
        let upper_verif_result =
            self.upper_proof
                .verify_single(bp_gens, pc_gens, transcript, upper_comm, n);
        if upper_verif_result.is_err() {
            return Err(RangeProofError::ProofError(upper_verif_result.unwrap_err()));
        }
        return Ok(true);
    }

    pub fn verify_multiple(
        &self,
        pc_gens: &PedersenGens,
        bp_gens: &BulletproofGens,
        transcript: &mut Transcript,
        lower_comms: &[blstrs::G1Affine],
        upper_comms: &[blstrs::G1Affine],
        n: usize,
    ) -> Result<bool, RangeProofError> {
        if lower_comms.len() == 0 || upper_comms.len() == 0 {
            return Err(RangeProofError::NoCommitments);
        } else if lower_comms.len() != upper_comms.len() {
            return Err(RangeProofError::MismatchingCommitmentLengths);
        }
        if n != self.n {
            return Err(RangeProofError::MismatchingParameters);
        }
        let lower_verif_result =
            self.lower_proof
                .verify_multiple(bp_gens, pc_gens, transcript, lower_comms, n);
        if lower_verif_result.is_err() {
            return Err(RangeProofError::ProofError(lower_verif_result.unwrap_err()));
        }
        let upper_verif_result =
            self.upper_proof
                .verify_multiple(bp_gens, pc_gens, transcript, upper_comms, n);
        if upper_verif_result.is_err() {
            return Err(RangeProofError::ProofError(upper_verif_result.unwrap_err()));
        }
        return Ok(true);
    }
}
