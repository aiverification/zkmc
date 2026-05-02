use bls::{GtElement, ZpElement};
use zkmatrix::utils::curve as bls;
use zkmatrix::utils::fiat_shamir::{TranElem, TranSeq};

use crate::utils::get_bls_gt_zero;

#[derive(Debug, Clone, PartialEq)]
pub struct EqualProof {
    pub t: GtElement,
    pub t_prime: GtElement,
    pub t_star: GtElement,
    pub z_i: Vec<ZpElement>,
    pub w: ZpElement,
    pub w_prime: ZpElement,
    pub w_star: ZpElement,
}

pub fn prove(
    v: &Vec<ZpElement>,
    g_i: &Vec<GtElement>,
    h: GtElement,
    r: ZpElement,
    g_prime_i: &Vec<GtElement>,
    h_prime: GtElement,
    r_prime: ZpElement,
    g_star_i: &Vec<GtElement>,
    h_star: GtElement,
    r_star: ZpElement,
    c: GtElement,
    c_prime: GtElement,
    c_star: GtElement,
) -> EqualProof {
    let l = v.len();
    let r_i: Vec<ZpElement> = (0..l).map(|_| ZpElement::rand()).collect();
    let rho = ZpElement::rand();
    let rho_prime = ZpElement::rand();
    let rho_star = ZpElement::rand();

    let mut t = get_bls_gt_zero();
    let mut t_prime = get_bls_gt_zero();
    let mut t_star = get_bls_gt_zero();
    for (i, rand) in r_i.iter().enumerate() {
        t += g_i[i] * *rand;
        t_prime += g_prime_i[i] * *rand;
        t_star += g_star_i[i] * *rand;
    }
    t += h * rho;
    t_prime += h_prime * rho_prime;
    t_star += h_star * rho_star;

    let mut transcript = TranSeq::new();
    transcript.push(TranElem::Gt(c));
    transcript.push(TranElem::Gt(c_prime));
    transcript.push(TranElem::Gt(c_star));
    transcript.push(TranElem::Gt(t));
    transcript.push(TranElem::Gt(t_prime));
    transcript.push(TranElem::Gt(t_star));
    let e = transcript.gen_challenge();

    let mut z_i = vec![];
    for i in 0..l {
        z_i.push(r_i[i] + (e * v[i]));
    }

    let w = rho + (e * r);
    let w_prime = rho_prime + (e * r_prime);
    let w_star = rho_star + (e * r_star);

    return EqualProof {
        t,
        t_prime,
        t_star,
        z_i,
        w,
        w_prime,
        w_star,
    };
}

impl EqualProof {
    //TODO - add abort checks in verify (IF t, h == zero OR t, h not on curve)
    pub fn verify(
        &self,
        g_i: &Vec<GtElement>,
        h: GtElement,
        g_prime_i: &Vec<GtElement>,
        h_prime: GtElement,
        g_star_i: &Vec<GtElement>,
        h_star: GtElement,
        c: GtElement,
        c_prime: GtElement,
        c_star: GtElement,
    ) -> bool {
        let mut transcript = TranSeq::new();
        transcript.push(TranElem::Gt(c));
        transcript.push(TranElem::Gt(c_prime));
        transcript.push(TranElem::Gt(c_star));
        transcript.push(TranElem::Gt(self.t));
        transcript.push(TranElem::Gt(self.t_prime));
        transcript.push(TranElem::Gt(self.t_star));
        let e = transcript.gen_challenge();

        let mut normal_lhs = get_bls_gt_zero();
        let mut prime_lhs = get_bls_gt_zero();
        let mut star_lhs = get_bls_gt_zero();
        for (i, z) in self.z_i.iter().enumerate() {
            normal_lhs += g_i[i] * *z;
            prime_lhs += g_prime_i[i] * *z;
            star_lhs += g_star_i[i] * *z;
        }
        normal_lhs += h * self.w;
        prime_lhs += h_prime * self.w_prime;
        star_lhs += h_star * self.w_star;

        let normal_rhs = self.t + (c * e);
        let prime_rhs = self.t_prime + (c_prime * e);
        let star_rhs = self.t_star + (c_star * e);

        if normal_lhs != normal_rhs || prime_lhs != prime_rhs || star_lhs != star_rhs {
            println!("Error verifying Equal");
            return false;
        }
        return true;
    }
}
