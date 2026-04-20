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


def vector_to_column_list(arr: np.ndarray | list) -> list:
    """Convert 1D array/list to column vector (list of single-element lists).

    Args:
        arr: 1D numpy array or list

    Returns:
        List of lists, e.g., [1, 2, 3] -> [[1], [2], [3]]
    """
    if isinstance(arr, np.ndarray):
        arr = arr.tolist()
    if not arr:  # Empty list
        return []
    return [[x] for x in arr]


def get_obligation_matrices(verifier: Verifier, obl_result) -> dict[str, np.ndarray]:
    """Reconstruct matrices for an obligation result.

    New simplified format returns:
        - A_s, b_s: Stacked premise (includes both prog transition and middle constraints)
        - E, f: Conclusion (single matrix/vector, not list)

    For the three obligation types:
    1. initial_non_infinity: A_s = A_0, E = E_k (from infinity case)
    2. transition_non_infinity: A_s = [A_i; P; C_j], E = E_k (from infinity case)
    3. update: A_s = [A_i; P; C_j; C_k], E = [w_j, -w_k]

    Returns:
        Dictionary with keys: A_s, b_s, E, f
    """
    n = len(verifier.variables)

    if obl_result.obligation_type == "initial_non_infinity":
        # Type 1: A_0 x ≤ b_0 => E_k x > f_k
        # No middle premise, so G_p = E_p
        state = obl_result.source_ranking_state
        rank_enc = verifier.rank_encs[state]
        inf_case = rank_enc.infinity_cases[obl_result.infinity_case_idx]

        return {
            "A_s": verifier.init_enc.A_0,  # SECRET
            "b_s": verifier.init_enc.b_0,
            "G_p": inf_case.E_k,            # PUBLIC: just conclusion (no middle premise)
            "h_p": inf_case.f_k
        }

    elif obl_result.obligation_type == "transition_non_infinity":
        # Type 2: A_i [x;x'] ≤ b_i => [P; C_j] x ≤ [r; d_j] => E_k x' > f_k
        # For transition (q, σ, q'): finite case j from rank(q), infinity case k from rank(q')
        prog_idx = obl_result.program_transition_idx
        from_state, to_state = obl_result.automaton_transition
        source_state = obl_result.source_ranking_state
        target_state = obl_result.target_ranking_state
        fin_case_idx = obl_result.source_case_idx
        inf_case_idx = obl_result.infinity_case_idx

        prog_trans = verifier.trans_encs[prog_idx]
        aut_trans = verifier.aut_encs[obl_result.automaton_transition_idx]
        source_rank_enc = verifier.rank_encs[source_state]
        target_rank_enc = verifier.rank_encs[target_state]
        fin_case = source_rank_enc.finite_cases[fin_case_idx]
        inf_case = target_rank_enc.infinity_cases[inf_case_idx]

        # Build middle premise: [P; C_j] (PUBLIC)
        P_exp, r_exp = verifier._align_and_expand(aut_trans.P, aut_trans.r, aut_trans.variables, primed=False)
        C_j_exp, d_j_exp = verifier._align_and_expand(fin_case.C_j, fin_case.d_j, source_rank_enc.variables, primed=False)

        middle_matrices = []
        middle_vectors = []

        if P_exp.shape[0] > 0:
            middle_matrices.append(P_exp)
            middle_vectors.append(r_exp)
        if C_j_exp.shape[0] > 0:
            middle_matrices.append(C_j_exp)
            middle_vectors.append(d_j_exp)

        C_p = np.vstack(middle_matrices) if middle_matrices else np.zeros((0, 2*n), dtype=np.int64)
        d_p = np.concatenate(middle_vectors) if middle_vectors else np.zeros(0, dtype=np.int64)

        # Expand E_k to [x;x'] space (check next state x')
        E_p, f_p = verifier._align_and_expand(inf_case.E_k, inf_case.f_k, target_rank_enc.variables, primed=True)

        # Build G_p = [C_p; E_p] (stack middle premise with conclusion)
        if C_p.shape[0] > 0 and E_p.shape[0] > 0:
            G_p = np.vstack([C_p, E_p])
            h_p = np.concatenate([d_p, f_p])
        elif C_p.shape[0] > 0:
            G_p = C_p
            h_p = d_p
        elif E_p.shape[0] > 0:
            G_p = E_p
            h_p = f_p
        else:
            G_p = np.zeros((0, 2*n), dtype=np.int64)
            h_p = np.zeros(0, dtype=np.int64)

        return {
            "A_s": prog_trans.A,  # SECRET: just program transition
            "b_s": prog_trans.b,
            "G_p": G_p,           # PUBLIC: [C_p; E_p]
            "h_p": h_p
        }

    elif obl_result.obligation_type == "update":
        # Type 3: A_i [x;x'] ≤ b_i => [P; C_j; C_k] [x;x'] ≤ [r; d_j; d_k] => [w_j, -w_k] [x;x'] > u_k - u_j + ζ
        prog_idx = obl_result.program_transition_idx
        from_state, to_state = obl_result.automaton_transition
        source_case_idx = obl_result.source_case_idx
        target_case_idx = obl_result.target_case_idx

        prog_trans = verifier.trans_encs[prog_idx]
        aut_trans = verifier.aut_encs[obl_result.automaton_transition_idx]
        source_enc = verifier.rank_encs[from_state]
        target_enc = verifier.rank_encs[to_state]
        source_case = source_enc.finite_cases[source_case_idx]
        target_case = target_enc.finite_cases[target_case_idx]

        zeta = 1 if aut_trans.is_fair else 0

        # Build middle premise: [P; C_j; C_k] (PUBLIC)
        P_exp, r_exp = verifier._align_and_expand(aut_trans.P, aut_trans.r, aut_trans.variables, primed=False)
        C_j_exp, d_j_exp = verifier._align_and_expand(source_case.C_j, source_case.d_j, source_enc.variables, primed=False)
        C_k_exp, d_k_exp = verifier._align_and_expand(target_case.C_j, target_case.d_j, target_enc.variables, primed=True)

        middle_matrices = []
        middle_vectors = []

        if P_exp.shape[0] > 0:
            middle_matrices.append(P_exp)
            middle_vectors.append(r_exp)
        if C_j_exp.shape[0] > 0:
            middle_matrices.append(C_j_exp)
            middle_vectors.append(d_j_exp)
        if C_k_exp.shape[0] > 0:
            middle_matrices.append(C_k_exp)
            middle_vectors.append(d_k_exp)

        C_p = np.vstack(middle_matrices) if middle_matrices else np.zeros((0, 2*n), dtype=np.int64)
        d_p = np.concatenate(middle_vectors) if middle_vectors else np.zeros(0, dtype=np.int64)

        # Build conclusion: [w_j, -w_k] [x;x'] > u_k - u_j + ζ (PUBLIC)
        w_j_exp = np.zeros(2*n, dtype=np.int64)
        w_k_exp = np.zeros(2*n, dtype=np.int64)

        for var_idx, var in enumerate(source_enc.variables):
            if var in verifier.variables:
                full_idx = verifier.variables.index(var)
                w_j_exp[full_idx] = source_case.w_j[var_idx]

        for var_idx, var in enumerate(target_enc.variables):
            if var in verifier.variables:
                full_idx = verifier.variables.index(var)
                w_k_exp[n + full_idx] = target_case.w_j[var_idx]

        E_p = (w_j_exp - w_k_exp).reshape(1, -1)
        # For ranking decrease ≥ ζ, we need V_j - V_k > ζ - 1 (strict inequality for integers)
        f_p = np.array([target_case.u_j - source_case.u_j + zeta - 1], dtype=np.int64)

        # Build G_p = [C_p; E_p] (stack middle premise with conclusion)
        if C_p.shape[0] > 0 and E_p.shape[0] > 0:
            G_p = np.vstack([C_p, E_p])
            h_p = np.concatenate([d_p, f_p])
        elif C_p.shape[0] > 0:
            G_p = C_p
            h_p = d_p
        elif E_p.shape[0] > 0:
            G_p = E_p
            h_p = f_p
        else:
            G_p = np.zeros((0, 2*n), dtype=np.int64)
            h_p = np.zeros(0, dtype=np.int64)

        return {
            "A_s": prog_trans.A,  # SECRET: just program transition
            "b_s": prog_trans.b,
            "G_p": G_p,           # PUBLIC: [C_p; E_p]
            "h_p": h_p
        }

    else:
        raise ValueError(f"Unknown obligation type: {obl_result.obligation_type}")


def obligation_to_json(verifier: Verifier, obl_result) -> dict[str, Any]:
    """Convert ObligationResult to JSON with all Farkas components.

    Uniform pattern format (matching paper notation):
    - Matrices: A_s, b_s (secret), G_p, h_p (public)
    - Witness: lambda_s, mu_s (if SAT)
    - Computed convenience value: -b_s^T lambda_s

    All obligations use uniform pattern:
        A_s y ≤ b_s ⟹ G_p y ≰ h_p
    """
    # Get the matrices by reconstructing the obligation
    matrices = get_obligation_matrices(verifier, obl_result)

    # Determine n_vars from matrices
    if matrices["A_s"].size > 0:
        n_vars = matrices["A_s"].shape[1]
    elif matrices["G_p"].size > 0:
        n_vars = matrices["G_p"].shape[1]
    else:
        n_vars = 0

    obj = {
        "obligation_type": obl_result.obligation_type,
        "matrices": {
            "A_s": numpy_to_list(matrices["A_s"]),     # SECRET: program transition only
            "b_s": vector_to_column_list(matrices["b_s"]),
            "G_p": numpy_to_list(matrices["G_p"]),     # PUBLIC: [C_p; E_p] stacked
            "h_p": vector_to_column_list(matrices["h_p"]),
        },
        "dimensions": {
            "n_vars": n_vars,
            "n_lambda_s": matrices["A_s"].shape[0],    # Multipliers for SECRET A_s
            "n_mu_s": matrices["G_p"].shape[0],        # Multipliers for PUBLIC G_p
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

    if obl_result.target_case_idx is not None:
        obj["target_case_idx"] = obl_result.target_case_idx

    if obl_result.infinity_case_idx is not None:
        obj["infinity_case_idx"] = obl_result.infinity_case_idx

    if obl_result.is_fair:
        obj["is_fair"] = obl_result.is_fair

    # Add witness and computed values if obligation passed
    if obl_result.passed and obl_result.witness:
        n_lambda_s = matrices["A_s"].shape[0]
        n_mu_s = matrices["G_p"].shape[0]

        # Extract witness vectors
        lambda_s = [obl_result.witness.get(f'lambda_s_{i}', 0) for i in range(n_lambda_s)]
        mu_s = [obl_result.witness.get(f'mu_s_{i}', 0) for i in range(n_mu_s)]

        # Compute convenience values for ZK proofs
        lambda_s_arr = np.array(lambda_s, dtype=np.int64)
        mu_s_arr = np.array(mu_s, dtype=np.int64)

        # -b_s^T · lambda_s
        neg_b_s_T_lambda_s = 0
        if matrices["b_s"].size > 0:
            neg_b_s_T_lambda_s = -int(np.dot(matrices["b_s"], lambda_s_arr))

        # -h_p^T · mu_s
        neg_h_p_T_mu_s = 0
        if matrices["h_p"].size > 0:
            neg_h_p_T_mu_s = -int(np.dot(matrices["h_p"], mu_s_arr))

        # A_s^T · lambda_s (result is a vector)
        A_s_T_lambda_s = []
        if matrices["A_s"].size > 0:
            A_s_T_lambda_s = np.dot(matrices["A_s"].T, lambda_s_arr).tolist()

        # G_p^T · mu_s (result is a vector)
        G_p_T_mu_s = []
        if matrices["G_p"].size > 0:
            G_p_T_mu_s = np.dot(matrices["G_p"].T, mu_s_arr).tolist()

        obj["witness"] = {
            "lambda_s": vector_to_column_list(lambda_s),
            "mu_s": vector_to_column_list(mu_s)
        }

        obj["computed_values"] = {
            "neg_b_s_T_lambda_s": neg_b_s_T_lambda_s,           # scalar
            "neg_h_p_T_mu_s": neg_h_p_T_mu_s,                   # scalar
            "A_s_T_lambda_s": vector_to_column_list(A_s_T_lambda_s),  # (m × 1) column vector
            "G_p_T_mu_s": vector_to_column_list(G_p_T_mu_s)           # (m × 1) column vector
        }

        obj["satisfiable"] = True
    else:
        obj["witness"] = None
        obj["computed_values"] = None
        obj["satisfiable"] = False

    return obj


def extract_farkas_obligations(
    file_path: str,
    const_overrides: dict[str, int] | None = None
) -> list[dict[str, Any]]:
    """Extract all Farkas dual formulations from a program file.

    Args:
        file_path: Path to .gc file
        const_overrides: Optional dict of constant overrides

    Returns:
        List of obligation dictionaries with Farkas components
    """
    text = Path(file_path).read_text()
    result = parse_with_constants(text, const_overrides=const_overrides)

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
    parser.add_argument(
        "--const",
        action="append",
        metavar="NAME=VALUE",
        help="Override constant value (e.g., --const maxVal=5). Can be used multiple times."
    )

    args = parser.parse_args(argv)

    try:
        # Parse constant overrides
        const_overrides = {}
        if args.const:
            for const_arg in args.const:
                try:
                    name, value = const_arg.split("=", 1)
                    const_overrides[name.strip()] = int(value.strip())
                except ValueError as e:
                    print(f"Error: Invalid constant override '{const_arg}'. Use format NAME=VALUE.", file=sys.stderr)
                    return 1

        # Parse the file to get constants
        text = Path(args.file).read_text()
        result = parse_with_constants(text, const_overrides=const_overrides if const_overrides else None)

        # Use Verifier to compute all obligations
        verifier = Verifier(result)
        verification = verifier.verify_all()

        # Convert to JSON format
        obligations = [
            obligation_to_json(verifier, obl)
            for obl in verification.obligations
        ]

        output = {
            "obligations": obligations,
            "count": len(obligations)
        }

        # Add constants for reproducibility (only if non-empty)
        if result.constants:
            output["constants"] = result.constants

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
