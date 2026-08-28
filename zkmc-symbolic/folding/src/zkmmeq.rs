use bls::{GtElement, ZpElement};
use zkmatrix::setup::SRS;
use zkmatrix::utils::curve as bls;
use zkmatrix::utils::fiat_shamir::{TranElem, TranSeq};

use crate::utils::curve_utils::get_bls_gt_zero;

#[derive(Debug, Clone, PartialEq)]
pub struct EqualProof {
    pub t_x: GtElement,
    pub t_j: Vec<GtElement>,
    pub z_i: Vec<ZpElement>,
    pub w_x: ZpElement,
    pub w_j: Vec<ZpElement>,
}

//We calculate g_hat_j and g_hat_x vectors inside ZKP itself
pub fn prove(
    sp: &SRS,
    c_j: &Vec<GtElement>,
    r_j: &Vec<ZpElement>,
    g_j: &Vec<&Vec<GtElement>>,
    x: &Vec<ZpElement>,
    g_x: &Vec<GtElement>,
    c_x: GtElement,
    r_x: ZpElement,
) -> EqualProof {
    let l = x.len();
    let j = c_j.len();
    assert!(c_j.len() == j && r_j.len() == j && g_j.len() == j);
    let r_i: Vec<ZpElement> = (0..l).map(|_| ZpElement::rand()).collect();

    let sigma_j: Vec<ZpElement> = c_j.iter().map(|_| ZpElement::rand()).collect();
    let sigma_x = ZpElement::rand();

    let mut t_x = get_bls_gt_zero();
    let mut t_j: Vec<GtElement> = c_j.iter().map(|_| get_bls_gt_zero()).collect();
    for (i, r) in r_i.iter().enumerate(){
        t_x += g_x[i] * *r;
        for k in 0..j{
            t_j[k] += g_j[k][i] * *r;
        }
    }
    t_x += sp.blind_base * sigma_x;
    for k in 0..j{
        t_j[k] += sp.blind_base * sigma_j[k];
    }

    //Order differs to paper but this doesn't matter as long as we are consistent
    let mut transcript = TranSeq::new();
    transcript.push(TranElem::Gt(c_x));
    transcript.push(TranElem::Gt(t_x));
    for k in 0..j{
        transcript.push(TranElem::Gt(c_j[k]));
        transcript.push(TranElem::Gt(t_j[k]));
    }
    let e = transcript.gen_challenge();

    let mut z_i = vec![];
    for i in 0..l {
        z_i.push(r_i[i] + (e * x[i]));
    }

    let w_x = sigma_x + (e * r_x);
    let mut w_j = vec![];
    for k in 0..j{
        w_j.push(sigma_j[k] + (e * r_j[k]));
    }

    return EqualProof {
        t_x,
        t_j,
        z_i,
        w_x,
        w_j,
    };
}

impl EqualProof {
    //TODO - add abort checks in verify (IF t, h == zero OR t, h not on curve)
    pub fn verify(
        &self,
        sp: &SRS,
        c_j: &Vec<GtElement>,
        g_j: &Vec<&Vec<GtElement>>,
        g_x: &Vec<GtElement>,
        c_x: GtElement,
    ) -> bool {
        let j = c_j.len();
        assert!(c_j.len() == j && g_j.len() == j);

        let mut transcript = TranSeq::new();
        transcript.push(TranElem::Gt(c_x));
        transcript.push(TranElem::Gt(self.t_x));
        for k in 0..j{
            transcript.push(TranElem::Gt(c_j[k]));
            transcript.push(TranElem::Gt(self.t_j[k]));
        }
        let e = transcript.gen_challenge();

        let mut lhs_x = get_bls_gt_zero();
        let mut lhs_j: Vec<GtElement> = c_j.iter().map(|_| get_bls_gt_zero()).collect();
        for (i, z) in self.z_i.iter().enumerate() {
            lhs_x += g_x[i] * *z;
            for k in 0..j{
                lhs_j[k] += g_j[k][i] * *z;
            }
        }

        lhs_x += sp.blind_base * self.w_x;
        for k in 0..j{
            lhs_j[k] += sp.blind_base * self.w_j[k];
        }
        let rhs_x = self.t_x + (c_x * e);
        let mut rhs_j = vec![];
        for k in 0..j{
            rhs_j.push(self.t_j[k] + (c_j[k] * e));
        }

        let mut error = false;
        if lhs_x != rhs_x{
            println!("lhs_x: {:?}\nrhs_x: {:?}\n", lhs_x, rhs_x);
            error = true;
        }
        for k in 0..j{
            if lhs_j[k] != rhs_j[k]{
                println!("lhs_{k:?}: {:?}\nrhs_{k:?}: {:?}\n", lhs_j[k], rhs_j[k]);
                error = true;
            }
        }

        if error {
            println!("Error verifying Equal");
            return false;
        }
        return true;
    }
}

pub fn derive_zkmmeq_matrix_bases(
    matrix: &zkmatrix::mat::Mat<ZpElement>,
    output_bases: &[GtElement],
) -> Vec<GtElement> {
    assert_eq!(matrix.shape.0, output_bases.len());

    let mut input_bases = vec![get_bls_gt_zero(); matrix.shape.1];

    for &(row, column, value) in &matrix.data {
        input_bases[column] += output_bases[row] * value;
    }

    input_bases
}
// ---------------------------------------------------------------------------
// G1 form.
//
// Every operand this routine touches -- the output bases, the bases derived
// from the public matrix, the commitments c_x and c^(j), and the blinding
// base -- is a pairing against srs.h_hat_vec[0] (see `commit.rs`). Running
// the same Chaum-Pedersen argument on the G1 preimages is therefore exactly
// the same protocol: the verification equation holds in G1 iff it holds in
// Gt, because pairing against a fixed non-trivial element is injective.
//
// It is also where nearly all of zkmmeq's cost was. Deriving the bases from
// G* is m*n scalar multiplications; at 767us each in Gt that was ~80% of the
// routine, and in G1 with a multi-scalar multiplication it is under a
// millisecond.
// ---------------------------------------------------------------------------

use bls::G1Element;

use crate::commit::g1_blind_base;
use crate::utils::curve_utils::get_bls_g1_zero;
use crate::utils::msm::msm_g1;

#[derive(Debug, Clone, PartialEq)]
pub struct EqualProofG1 {
    pub t_x: G1Element,
    pub t_j: Vec<G1Element>,
    pub z_i: Vec<ZpElement>,
    pub w_x: ZpElement,
    pub w_j: Vec<ZpElement>,
}

/// Encodes a public matrix into commitment bases:
/// `input[col] = sum_row matrix[row][col] * output[row]`,
/// so that `Com(x; input) == Com(matrix * x; output)`.
pub fn derive_matrix_bases_g1(
    matrix: &zkmatrix::mat::Mat<ZpElement>,
    output_bases: &[G1Element],
) -> Vec<G1Element> {
    assert_eq!(matrix.shape.0, output_bases.len());
    let columns = matrix.shape.1;

    let mut by_column: Vec<Vec<(G1Element, ZpElement)>> = vec![Vec::new(); columns];
    for &(row, column, value) in &matrix.data {
        if value != ZpElement::from(0u64) {
            by_column[column].push((output_bases[row], value));
        }
    }

    by_column
        .into_iter()
        .map(|terms| {
            let (points, scalars): (Vec<G1Element>, Vec<ZpElement>) = terms.into_iter().unzip();
            msm_g1(&points, &scalars)
        })
        .collect()
}

/// The commitment bases for a column vector of the given length: the same
/// `g_hat_vec` prefix every column commitment uses.
pub fn column_bases_g1(sp: &SRS, length: usize) -> Vec<G1Element> {
    assert!(sp.g_hat_vec.len() >= length);
    sp.g_hat_vec[..length].to_vec()
}

pub fn prove_g1(
    sp: &SRS,
    c_j: &[G1Element],
    r_j: &[ZpElement],
    g_j: &[Vec<G1Element>],
    x: &[ZpElement],
    g_x: &[G1Element],
    c_x: G1Element,
    r_x: ZpElement,
) -> EqualProofG1 {
    let l = x.len();
    let j = c_j.len();
    assert!(r_j.len() == j && g_j.len() == j);
    assert!(g_x.len() == l);
    for bases in g_j.iter() {
        assert_eq!(bases.len(), l);
    }

    let blind = g1_blind_base(sp);
    let r_i: Vec<ZpElement> = (0..l).map(|_| ZpElement::rand()).collect();
    let sigma_j: Vec<ZpElement> = (0..j).map(|_| ZpElement::rand()).collect();
    let sigma_x = ZpElement::rand();

    let t_x = commit_with_blind(g_x, &r_i, blind, sigma_x);
    let t_j: Vec<G1Element> = g_j
        .iter()
        .zip(sigma_j.iter())
        .map(|(bases, sigma)| commit_with_blind(bases, &r_i, blind, *sigma))
        .collect();

    let mut transcript = TranSeq::new();
    transcript.push(TranElem::G1(c_x));
    transcript.push(TranElem::G1(t_x));
    for k in 0..j {
        transcript.push(TranElem::G1(c_j[k]));
        transcript.push(TranElem::G1(t_j[k]));
    }
    let e = transcript.gen_challenge();

    let z_i: Vec<ZpElement> = (0..l).map(|i| r_i[i] + (e * x[i])).collect();
    let w_x = sigma_x + (e * r_x);
    let w_j: Vec<ZpElement> = (0..j).map(|k| sigma_j[k] + (e * r_j[k])).collect();

    EqualProofG1 {
        t_x,
        t_j,
        z_i,
        w_x,
        w_j,
    }
}

fn commit_with_blind(
    bases: &[G1Element],
    scalars: &[ZpElement],
    blind: G1Element,
    randomness: ZpElement,
) -> G1Element {
    let mut points = bases.to_vec();
    points.push(blind);
    let mut weights = scalars.to_vec();
    weights.push(randomness);
    msm_g1(&points, &weights)
}

impl EqualProofG1 {
    pub fn verify(
        &self,
        sp: &SRS,
        c_j: &[G1Element],
        g_j: &[Vec<G1Element>],
        g_x: &[G1Element],
        c_x: G1Element,
    ) -> bool {
        let j = c_j.len();
        if g_j.len() != j || self.t_j.len() != j || self.w_j.len() != j {
            println!("zkmmeq: malformed proof shape");
            return false;
        }
        if self.z_i.len() != g_x.len() {
            println!("zkmmeq: response length does not match the bases");
            return false;
        }

        // Abort checks: a degenerate commitment carries no information and
        // must not be accepted.
        let zero = get_bls_g1_zero();
        if self.t_x == zero || self.t_j.iter().any(|t| *t == zero) {
            println!("zkmmeq: degenerate commitment in proof");
            return false;
        }

        let blind = g1_blind_base(sp);

        let mut transcript = TranSeq::new();
        transcript.push(TranElem::G1(c_x));
        transcript.push(TranElem::G1(self.t_x));
        for k in 0..j {
            transcript.push(TranElem::G1(c_j[k]));
            transcript.push(TranElem::G1(self.t_j[k]));
        }
        let e = transcript.gen_challenge();

        if commit_with_blind(g_x, &self.z_i, blind, self.w_x) != self.t_x + (c_x * e) {
            println!("zkmmeq: failed equality check on x");
            return false;
        }
        for k in 0..j {
            if commit_with_blind(&g_j[k], &self.z_i, blind, self.w_j[k])
                != self.t_j[k] + (c_j[k] * e)
            {
                println!("zkmmeq: failed equality check on output {k}");
                return false;
            }
        }
        true
    }
}
