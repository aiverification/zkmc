"""CLI for outputting Farkas dual formulations as JSON."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

from .parser import parse_with_constants
from .verifier import Verifier


def numpy_to_list(arr: np.ndarray) -> list:
    """Convert numpy array to nested Python list."""
    return arr.tolist()


def obligation_to_json(verifier: Verifier, obl_result) -> dict[str, Any]:
    """Convert ObligationResult to JSON with all Farkas components.

    Returns obligation with:
    - Matrices: A_s, b_s, A_p, b_p, C_p, d_p
    - Witness: lambda_s, lambda_p, mu_p (if SAT)
    - Computed values: alpha_p, beta_p, neg_bs_lambda_s (if SAT)
    """
    # Get the matrices by reconstructing the obligation
    # We need to access verifier's internal data to get the matrices
    matrices = verifier._get_obligation_matrices(obl_result)

    obj = {
        "obligation_type": obl_result.obligation_type,
        "matrices": {
            "A_s": numpy_to_list(matrices["A_s"]),
            "b_s": numpy_to_list(matrices["b_s"]),
            "A_p": numpy_to_list(matrices["A_p"]),
            "b_p": numpy_to_list(matrices["b_p"]),
            "C_p": numpy_to_list(matrices["C_p"]),
            "d_p": numpy_to_list(matrices["d_p"]),
        },
        "dimensions": {
            "n_vars": matrices["A_s"].shape[1] if matrices["A_s"].size > 0 else matrices["C_p"].shape[1],
            "n_lambda_s": matrices["A_s"].shape[0],
            "n_lambda_p": matrices["A_p"].shape[0],
            "n_mu_p": matrices["C_p"].shape[0],
        }
    }

    # Add metadata
    if obl_result.program_transition_idx is not None:
        obj["program_transition"] = obl_result.program_transition_idx

    if obl_result.automaton_transition:
        obj["automaton_transition"] = {
            "from": obl_result.automaton_transition[0],
            "to": obl_result.automaton_transition[1]
        }

    if obl_result.ranking_state:
        obj["ranking_state"] = obl_result.ranking_state

    # Add witness and computed values if obligation passed
    if obl_result.passed and obl_result.witness:
        n_lambda_s = matrices["A_s"].shape[0]
        n_lambda_p = matrices["A_p"].shape[0]
        n_mu_p = matrices["C_p"].shape[0]
        n_vars = matrices["A_s"].shape[1] if matrices["A_s"].size > 0 else matrices["C_p"].shape[1]

        # Extract witness vectors
        lambda_s = [obl_result.witness.get(f'lambda_s_{i}', 0) for i in range(n_lambda_s)]
        lambda_p = [obl_result.witness.get(f'lambda_p_{i}', 0) for i in range(n_lambda_p)]
        mu_p = [obl_result.witness.get(f'mu_p_{i}', 0) for i in range(n_mu_p)]

        # Compute aggregated terms
        lambda_s_arr = np.array(lambda_s, dtype=np.int64)
        lambda_p_arr = np.array(lambda_p, dtype=np.int64)
        mu_p_arr = np.array(mu_p, dtype=np.int64)

        # alpha_p = A_p^T * lambda_p + C_p^T * mu_p
        alpha_p = np.zeros(n_vars, dtype=np.int64)
        if matrices["A_p"].size > 0:
            alpha_p += np.dot(matrices["A_p"].T, lambda_p_arr)
        if matrices["C_p"].size > 0:
            alpha_p += np.dot(matrices["C_p"].T, mu_p_arr)

        # beta_p = b_p^T * lambda_p + d_p^T * mu_p
        beta_p = 0
        if matrices["b_p"].size > 0:
            beta_p += np.dot(matrices["b_p"], lambda_p_arr)
        if matrices["d_p"].size > 0:
            beta_p += np.dot(matrices["d_p"], mu_p_arr)

        # -b_s^T * lambda_s
        neg_bs_lambda_s = 0
        if matrices["b_s"].size > 0:
            neg_bs_lambda_s = -np.dot(matrices["b_s"], lambda_s_arr)

        obj["witness"] = {
            "lambda_s": lambda_s,
            "lambda_p": lambda_p,
            "mu_p": mu_p
        }

        obj["computed_values"] = {
            "alpha_p": numpy_to_list(alpha_p),
            "beta_p": int(beta_p),
            "neg_bs_lambda_s": int(neg_bs_lambda_s)
        }

        obj["satisfiable"] = True
    else:
        obj["witness"] = None
        obj["computed_values"] = None
        obj["satisfiable"] = False

    return obj


def extract_farkas_obligations(file_path: str) -> list[dict[str, Any]]:
    """Extract all Farkas dual formulations from a program file.

    Args:
        file_path: Path to .gc file

    Returns:
        List of obligation dictionaries with Farkas components
    """
    text = Path(file_path).read_text()
    result = parse_with_constants(text)

    # Use Verifier to compute all obligations
    verifier = Verifier(result)
    verification = verifier.verify_all()

    # Convert to JSON format
    obligations = [
        obligation_to_json(verifier, obl)
        for obl in verification.obligations
    ]

    return obligations


def main(argv: list[str] | None = None) -> int:
    """Main entry point for zkfarkas command."""
    parser = argparse.ArgumentParser(
        description="Output Farkas dual formulations as JSON",
        epilog="""
Example:
  zkfarkas program.gc
  zkfarkas --pretty program.gc
  zkfarkas program.gc > obligations.json
        """
    )
    parser.add_argument(
        "file",
        type=str,
        help="Input .gc file with program, ranking functions, and automaton transitions"
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output with indentation"
    )

    args = parser.parse_args(argv)

    try:
        obligations = extract_farkas_obligations(args.file)

        output = {
            "obligations": obligations,
            "count": len(obligations)
        }

        if args.pretty:
            print(json.dumps(output, indent=2))
        else:
            print(json.dumps(output))

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
