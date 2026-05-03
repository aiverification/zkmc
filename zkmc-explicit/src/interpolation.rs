use ark_bls12_381::Bls12_381 as bls;
use ark_ec::pairing::Pairing;
use ark_poly;
use ark_poly::EvaluationDomain;
pub type F = <bls as Pairing>::ScalarField;

// Takes in list of values to interpolate
// Returns polynomial, coset_offset(), group_gen()
pub fn interpolate(values: &Vec<F>) -> (ark_poly::univariate::DensePolynomial<F>, F, F) {
    let mut mut_values = values.clone();
    let n = mut_values.len();
    let domain =
        ark_poly::domain::GeneralEvaluationDomain::<F>::new(n).expect("no domain of this size");
    domain.ifft_in_place(&mut mut_values);
    return (
        ark_poly::univariate::DensePolynomial { coeffs: mut_values },
        domain.coset_offset(),
        domain.group_gen(),
    );
}
