"""Z3 SMT solver interface for Farkas dual constraints.

This module provides functions to solve Farkas dual formulations using
the Z3 SMT solver with integer linear arithmetic.
"""

import numpy as np
from numpy.typing import NDArray
from z3 import Int, Solver, sat

from .farkas import FarkasDual


def solve_farkas_dual(
    dual: FarkasDual,
    max_value: int = 2**32 - 1,
) -> tuple[bool, dict[str, int] | None]:
    """Solve Farkas dual formulation using Z3.

    Given a FarkasDual formulation, finds integer values for Farkas multipliers
    (λ_s, μ_p) that satisfy:
        1. A_eq @ multipliers = b_eq  (equality constraints)
        2. const_coeffs @ multipliers ≤ -1  (constant inequality)
        3. multipliers[i] ≥ 0 for i in lambda_s_indices, mu_p_indices
        4. 0 ≤ multipliers[i] < max_value (bounded for finite field proofs)

    Args:
        dual: FarkasDual object containing the dual formulation
        max_value: Upper bound for multipliers (default: 2^32 - 1)

    Returns:
        (satisfiable, witness) where:
            - satisfiable: True if SAT, False if UNSAT
            - witness: Dictionary mapping variable names to integer values if SAT,
                      None if UNSAT
    """
    # Total number of multipliers
    total_multipliers = len(dual.lambda_s_indices) + len(dual.mu_p_indices)

    # Create Z3 integer variables for all multipliers
    z3_vars = [Int(f"v{i}") for i in range(total_multipliers)]

    # Create solver
    solver = Solver()

    # Add equality constraints: A_eq @ multipliers = b_eq
    for row_idx in range(dual.A_eq.shape[0]):
        # Build linear expression for this row
        expr_terms = []
        for col_idx in range(dual.A_eq.shape[1]):
            coeff = int(dual.A_eq[row_idx, col_idx])
            if coeff != 0:
                expr_terms.append(coeff * z3_vars[col_idx])

        if expr_terms:
            lhs = sum(expr_terms[1:], expr_terms[0]) if len(expr_terms) > 1 else expr_terms[0]
        else:
            lhs = 0

        rhs = int(dual.b_eq[row_idx])
        solver.add(lhs == rhs)

    # Add constant inequality: const_coeffs @ multipliers ≤ -1
    const_terms = []
    for idx in range(len(dual.const_coeffs)):
        coeff = int(dual.const_coeffs[idx])
        if coeff != 0:
            const_terms.append(coeff * z3_vars[idx])

    if const_terms:
        const_lhs = sum(const_terms[1:], const_terms[0]) if len(const_terms) > 1 else const_terms[0]
        solver.add(const_lhs <= -1)

    # Add non-negativity and bounds for all multipliers
    all_nonneg_indices = dual.lambda_s_indices + dual.mu_p_indices
    for idx in all_nonneg_indices:
        solver.add(z3_vars[idx] >= 0)
        solver.add(z3_vars[idx] < max_value)

    # Check satisfiability
    result = solver.check()

    if result == sat:
        # Extract model values as witness
        model = solver.model()
        witness = {}

        # Lambda_s multipliers
        for i, idx in enumerate(dual.lambda_s_indices):
            witness[f"lambda_s_{i}"] = model[z3_vars[idx]].as_long()

        # Mu_p multipliers
        for i, idx in enumerate(dual.mu_p_indices):
            witness[f"mu_p_{i}"] = model[z3_vars[idx]].as_long()

        return True, witness
    else:
        return False, None
