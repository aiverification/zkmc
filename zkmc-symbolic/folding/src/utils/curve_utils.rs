use zkmatrix::utils::curve::{G1Element, G2Element, GtElement, ZpElement};
use bls12_381 as bls12;
use blstrs;

pub fn get_bls_g1_zero() -> G1Element {
    let P = G1Element::generator();
    return P - P;
}

pub fn get_bls_g2_zero() -> G2Element {
    let P = G2Element::generator();
    return P - P;
}

pub fn get_bls_gt_zero() -> GtElement {
    let P = GtElement::generator();
    return P - P;
}

pub fn blstrs_to_bls_field_elem(zp: &blstrs::Scalar) -> ZpElement {
    let bytes = zp.to_bytes_le();
    // Pad to 32 bytes if needed
    let mut buf = [0u8; 32];
    buf[(32 - bytes.len())..].copy_from_slice(&bytes);
    let scalar = bls12_381::Scalar::from_bytes(&buf).unwrap();
    return ZpElement { value: scalar };
}

pub fn blstrs_proj_to_bls_g1(g: &blstrs::G1Projective) -> G1Element {
    let g_aff = g.to_compressed();
    let bls_g_aff = bls12::G1Affine::from_compressed(&g_aff).unwrap();
    let bls_g = bls12::G1Projective::from(bls_g_aff);
    return G1Element { value: bls_g };
}

pub fn blstrs_affine_to_bls_g1(g: &blstrs::G1Affine) -> G1Element {
    let bls_g_aff = bls12::G1Affine::from_compressed(&g.to_compressed()).unwrap();
    return G1Element {
        value: bls12::G1Projective::from(bls_g_aff),
    };
}

pub fn bls_field_elem_to_blstrs_scalar(
    value: &zkmatrix::utils::curve::ZpElement,
) -> blstrs::Scalar {
    let bytes = value.value.to_bytes();

    Option::from(blstrs::Scalar::from_bytes_le(&bytes))
        .expect("ZpElement is not a valid blstrs scalar")
}