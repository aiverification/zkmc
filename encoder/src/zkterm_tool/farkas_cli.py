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

    New disjunctive format returns:
    - Matrices: A_s, b_s, C, d, E_list, f_list
    - Witness: lambda_s, mu_p (if SAT) - no lambda_p in new system
    - Computed values: alpha_p, beta_p, neg_bs_lambda_s (if SAT)

    The disjunctive format represents:
        ∀y: A_s y ≤ b_s ⟹ C y ≤ d ⟹ ∨_{k=1}^m E_k y > f_k
    """
    # Get the matrices by reconstructing the obligation
    matrices = verifier._get_obligation_matrices(obl_result)

    # Convert E_list and f_list to JSON-serializable format
    E_list_json = [numpy_to_list(E_k) for E_k in matrices["E_list"]]
    f_list_json = [numpy_to_list(f_k) for f_k in matrices["f_list"]]

    # Determine n_vars from matrices
    if matrices["A_s"].size > 0:
        n_vars = matrices["A_s"].shape[1]
    elif matrices["C"].size > 0:
        n_vars = matrices["C"].shape[1]
    elif len(matrices["E_list"]) > 0:
        n_vars = matrices["E_list"][0].shape[1]
    else:
        n_vars = 0

    # Count total rows in C_p = [C; E_1; E_2; ...; E_m]
    # μ_p multipliers correspond to all rows in C_p
    total_mu_p_rows = matrices["C"].shape[0] + sum(E_k.shape[0] for E_k in matrices["E_list"])

    obj = {
        "obligation_type": obl_result.obligation_type,
        "matrices": {
            "A_s": numpy_to_list(matrices["A_s"]),
            "b_s": numpy_to_list(matrices["b_s"]),
            "C": numpy_to_list(matrices["C"]),
            "d": numpy_to_list(matrices["d"]),
            "E_list": E_list_json,
            "f_list": f_list_json,
        },
        "dimensions": {
            "n_vars": n_vars,
            "n_lambda_s": matrices["A_s"].shape[0],
            "n_middle": matrices["C"].shape[0],
            "n_disjuncts": len(matrices["E_list"]),
            "n_mu_p": total_mu_p_rows,
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

    if obl_result.source_ranking_state:
        obj["source_ranking_state"] = obl_result.source_ranking_state

    if obl_result.target_ranking_state:
        obj["target_ranking_state"] = obl_result.target_ranking_state

    if obl_result.source_case_idx is not None:
        obj["source_case_idx"] = obl_result.source_case_idx

    if obl_result.is_fair:
        obj["is_fair"] = obl_result.is_fair

    # Add witness and computed values if obligation passed
    if obl_result.passed and obl_result.witness:
        n_lambda_s = matrices["A_s"].shape[0]
        n_mu_p = total_mu_p_rows  # Total rows across all E_k matrices

        # Extract witness vectors (only lambda_s and mu_p in new system)
        lambda_s = [obl_result.witness.get(f'lambda_s_{i}', 0) for i in range(n_lambda_s)]
        mu_p = [obl_result.witness.get(f'mu_p_{i}', 0) for i in range(n_mu_p)]

        # Compute aggregated terms
        lambda_s_arr = np.array(lambda_s, dtype=np.int64)
        mu_p_arr = np.array(mu_p, dtype=np.int64)

        # Build C_p = [C; E_1; E_2; ...; E_m] for computing alpha_p
        C_p_parts = [matrices["C"]] + matrices["E_list"]
        C_p_stacked = np.vstack(C_p_parts) if any(m.size > 0 for m in C_p_parts) else np.zeros((0, n_vars), dtype=np.int64)

        # Build d_p = [d; f_1; f_2; ...; f_m] for computing beta_p
        d_p_parts = [matrices["d"]] + matrices["f_list"]
        d_p_stacked = np.concatenate(d_p_parts) if any(v.size > 0 for v in d_p_parts) else np.zeros(0, dtype=np.int64)

        # alpha_p = [A_s^T; C_p^T] * [lambda_s; mu_p] = A_s^T * lambda_s + C_p^T * mu_p
        alpha_p = np.zeros(n_vars, dtype=np.int64)
        if matrices["A_s"].size > 0:
            alpha_p += np.dot(matrices["A_s"].T, lambda_s_arr)
        if C_p_stacked.size > 0:
            alpha_p += np.dot(C_p_stacked.T, mu_p_arr)

        # beta_p = [b_s; d_p]^T * [lambda_s; mu_p] = b_s^T * lambda_s + d_p^T * mu_p
        beta_p = 0
        if matrices["b_s"].size > 0:
            beta_p += np.dot(matrices["b_s"], lambda_s_arr)
        if d_p_stacked.size > 0:
            beta_p += np.dot(d_p_stacked, mu_p_arr)

        obj["witness"] = {
            "lambda_s": lambda_s,
            "mu_p": mu_p
        }

        obj["computed_values"] = {
            "alpha_p": numpy_to_list(alpha_p),
            "beta_p": int(beta_p),
            "verification_check": {
                "alpha_p_equals_zero": bool((alpha_p == 0).all()),
                "beta_p_leq_minus_one": bool(beta_p <= -1)
            }
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
