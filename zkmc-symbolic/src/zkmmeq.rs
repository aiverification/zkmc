use bls::{GtElement, ZpElement};
use zkmatrix::utils::curve as bls;
use zkmatrix::utils::fiat_shamir::{TranElem, TranSeq};

use crate::utils::get_bls_gt_zero;
use crate::zkp::ZkpSRS;

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
    sp: &ZkpSRS,
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
    t_x += sp.zk_matrix_srs.blind_base * sigma_x;
    for k in 0..j{
        t_j[k] += sp.zk_matrix_srs.blind_base * sigma_j[k];
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
        sp: &ZkpSRS,
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

        lhs_x += sp.zk_matrix_srs.blind_base * self.w_x;
        for k in 0..j{
            lhs_j[k] += sp.zk_matrix_srs.blind_base * self.w_j[k];
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
