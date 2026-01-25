"""Validation of ranking function properties.

This module provides validation functions to check that ranking functions satisfy
required properties for correctness:

1. Disjoint case constraints: All case guards are pairwise disjoint
2. Complete coverage: Cases cover the entire state space
3. Non-negativity: Finite cases are non-negative under their guards

All validation uses Z3 SMT solver to check properties symbolically.
"""

from typing import List, Tuple
import numpy as np
from numpy.typing import NDArray
import z3

from .ranking_encoder import RankingCaseEncoding, InfinityCaseEncoding


def check_disjoint_cases(
    finite_cases: List[RankingCaseEncoding],
    infinity_cases: List[InfinityCaseEncoding],
    variables: List[str]
) -> Tuple[bool, str]:
    """Check that all case constraints are pairwise disjoint.

    Verifies: For all i ≠ j: ¬∃x : guard_i(x) ∧ guard_j(x)

    This ensures each state satisfies at most one case guard, which is required
    for well-defined piecewise ranking functions.

    Args:
        finite_cases: List of finite case encodings (guards: C_j x ≤ d_j)
        infinity_cases: List of infinity case encodings (guards: E_k x ≤ f_k)
        variables: Ordered list of variable names

    Returns:
        (is_disjoint, error_message):
            - is_disjoint: True if all cases are pairwise disjoint, False otherwise
            - error_message: Empty string if disjoint, otherwise describes the violation
                             with a witness state showing overlap
    """
    solver = z3.Solver()

    # Create Z3 variables
    z3_vars = {var: z3.Int(var) for var in variables}
    x = [z3_vars[var] for var in variables]  # Ordered list matching matrix columns

    # Collect all guards (both finite and infinity)
    all_guards = []
    guard_labels = []

    # Add finite case guards
    for i, case in enumerate(finite_cases):
        all_guards.append((case.C_j, case.d_j))
        guard_labels.append(f"finite case {i}")

    # Add infinity case guards
    for k, case in enumerate(infinity_cases):
        all_guards.append((case.E_k, case.f_k))
        guard_labels.append(f"infinity case {k}")

    # Check all pairs for overlap
    for i in range(len(all_guards)):
        for j in range(i + 1, len(all_guards)):
            C_i, d_i = all_guards[i]
            C_j, d_j = all_guards[j]

            # Reset solver for each pair
            solver.reset()

            # Add guard_i constraints: C_i x ≤ d_i
            for row_idx in range(C_i.shape[0]):
                lhs = sum(int(C_i[row_idx, col]) * x[col] for col in range(len(x)))
                solver.add(lhs <= int(d_i[row_idx]))

            # Add guard_j constraints: C_j x ≤ d_j
            for row_idx in range(C_j.shape[0]):
                lhs = sum(int(C_j[row_idx, col]) * x[col] for col in range(len(x)))
                solver.add(lhs <= int(d_j[row_idx]))

            # Check if both guards can be satisfied simultaneously
            result = solver.check()

            if result == z3.sat:
                # Found overlap - get witness
                model = solver.model()
                witness = {var: model.evaluate(z3_vars[var], model_completion=True) for var in variables}
                witness_str = ", ".join(f"{var}={val}" for var, val in witness.items())

                error_msg = (
                    f"Cases are not disjoint: {guard_labels[i]} and {guard_labels[j]} "
                    f"overlap at state [{witness_str}]"
                )
                return (False, error_msg)

    return (True, "")


def check_complete_coverage(
    finite_cases: List[RankingCaseEncoding],
    infinity_cases: List[InfinityCaseEncoding],
    variables: List[str]
) -> Tuple[bool, str]:
    """Check that cases cover the entire state space.

    Verifies: ∀x : guard_1(x) ∨ ... ∨ guard_m(x) ∨ guard_{m+1}(x) ∨ ... ∨ guard_{m+l}(x)

    This ensures every state has a defined ranking value (finite or +∞).

    Args:
        finite_cases: List of finite case encodings
        infinity_cases: List of infinity case encodings
        variables: Ordered list of variable names

    Returns:
        (is_complete, error_message):
            - is_complete: True if cases cover all states, False otherwise
            - error_message: Empty string if complete, otherwise describes the gap
                             with a witness state not covered by any case
    """
    solver = z3.Solver()

    # Create Z3 variables
    z3_vars = {var: z3.Int(var) for var in variables}
    x = [z3_vars[var] for var in variables]  # Ordered list

    # We want to check if there exists a state not covered by any guard
    # That is: ∃x : ¬(guard_1 ∨ ... ∨ guard_n)
    # Equivalent to: ∃x : ¬guard_1 ∧ ... ∧ ¬guard_n

    # For each guard, add constraint: NOT (C_i x ≤ d_i)
    # Which is equivalent to: ∃j : C_i[j] x > d_i[j]

    for case in finite_cases:
        C, d = case.C_j, case.d_j
        if C.shape[0] == 0:
            # Empty guard = always true, so coverage is complete
            return (True, "")

        # NOT (C x ≤ d) means: at least one row violates the guard
        # That is: ∃row : C[row] x > d[row]
        # We express this as: OR over all rows
        violations = []
        for row_idx in range(C.shape[0]):
            lhs = sum(int(C[row_idx, col]) * x[col] for col in range(len(x)))
            violations.append(lhs > int(d[row_idx]))

        # Guard is violated if ANY row is violated
        solver.add(z3.Or(*violations))

    for case in infinity_cases:
        E, f = case.E_k, case.f_k
        if E.shape[0] == 0:
            # Empty guard = always true, so coverage is complete
            return (True, "")

        violations = []
        for row_idx in range(E.shape[0]):
            lhs = sum(int(E[row_idx, col]) * x[col] for col in range(len(x)))
            violations.append(lhs > int(f[row_idx]))

        solver.add(z3.Or(*violations))

    # Check if there exists a state not covered by any guard
    result = solver.check()

    if result == z3.sat:
        # Found a gap - get witness
        model = solver.model()
        witness = {var: model.evaluate(z3_vars[var], model_completion=True) for var in variables}
        witness_str = ", ".join(f"{var}={val}" for var, val in witness.items())

        error_msg = (
            f"Cases do not cover entire state space: "
            f"state [{witness_str}] is not covered by any case"
        )
        return (False, error_msg)

    return (True, "")


def check_non_negativity(
    finite_cases: List[RankingCaseEncoding],
    variables: List[str]
) -> Tuple[bool, str]:
    """Check that each finite case is non-negative under its guard.

    Verifies: For all k: C_k x ≤ d_k => w_k x + u_k ≥ 0

    This ensures finite ranking values are always non-negative, which is required
    for soundness of the termination argument.

    Args:
        finite_cases: List of finite case encodings
        variables: Ordered list of variable names

    Returns:
        (is_non_negative, error_message):
            - is_non_negative: True if all finite cases are non-negative under their guards
            - error_message: Empty string if non-negative, otherwise describes the violation
                             with a witness state showing negative ranking value
    """
    solver = z3.Solver()

    # Create Z3 variables
    z3_vars = {var: z3.Int(var) for var in variables}
    x = [z3_vars[var] for var in variables]  # Ordered list

    # Check each finite case
    for case_idx, case in enumerate(finite_cases):
        solver.reset()

        # Add guard constraints: C_k x ≤ d_k
        for row_idx in range(case.C_j.shape[0]):
            lhs = sum(int(case.C_j[row_idx, col]) * x[col] for col in range(len(x)))
            solver.add(lhs <= int(case.d_j[row_idx]))

        # Add constraint: ranking expression is negative (w_k x + u_k < 0)
        ranking_expr = sum(int(case.w_j[col]) * x[col] for col in range(len(x))) + int(case.u_j)
        solver.add(ranking_expr < 0)

        # Check if guard AND negative ranking is satisfiable
        result = solver.check()

        if result == z3.sat:
            # Found a state where guard is satisfied but ranking is negative
            model = solver.model()
            witness = {var: model.evaluate(z3_vars[var], model_completion=True) for var in variables}
            witness_str = ", ".join(f"{var}={val}" for var, val in witness.items())

            ranking_val = sum(int(case.w_j[col]) * int(model.evaluate(z3_vars[variables[col]], model_completion=True).as_long())
                            for col in range(len(x))) + int(case.u_j)

            error_msg = (
                f"Finite case {case_idx} has negative ranking value: "
                f"at state [{witness_str}], ranking = {ranking_val}"
            )
            return (False, error_msg)

    return (True, "")


def validate_ranking_function(
    finite_cases: List[RankingCaseEncoding],
    infinity_cases: List[InfinityCaseEncoding],
    variables: List[str]
) -> Tuple[bool, List[str]]:
    """Validate all required properties of a ranking function.

    Checks:
    1. Disjoint case constraints
    2. Complete coverage
    3. Non-negativity (for finite cases)

    Args:
        finite_cases: List of finite case encodings
        infinity_cases: List of infinity case encodings
        variables: Ordered list of variable names

    Returns:
        (all_valid, error_messages):
            - all_valid: True if all validation checks pass
            - error_messages: List of error messages (empty if all valid)
    """
    errors = []

    # Check 1: Disjointness
    is_disjoint, disjoint_msg = check_disjoint_cases(finite_cases, infinity_cases, variables)
    if not is_disjoint:
        errors.append(f"Disjointness check failed: {disjoint_msg}")

    # Check 2: Coverage
    is_complete, coverage_msg = check_complete_coverage(finite_cases, infinity_cases, variables)
    if not is_complete:
        errors.append(f"Coverage check failed: {coverage_msg}")

    # Check 3: Non-negativity
    is_non_negative, non_neg_msg = check_non_negativity(finite_cases, variables)
    if not is_non_negative:
        errors.append(f"Non-negativity check failed: {non_neg_msg}")

    return (len(errors) == 0, errors)
