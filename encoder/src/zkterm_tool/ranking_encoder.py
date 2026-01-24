"""Encode ranking functions as matrix/vector forms.

Given a ranking function V(x, q) as piecewise linear cases:
    rank(q):
        [] guard_1 -> expression_1
        [] guard_2 -> expression_2
        ...

We encode each case j as:
    - (A_j, b_j) for guard: A_j x ≤ b_j
    - (C_j, d_j) for expression: C_j x + d_j

Where:
    - A_j is a matrix (guard can have multiple inequalities)
    - b_j is a vector
    - C_j is a row vector (expression returns scalar)
    - d_j is a scalar
"""

from dataclasses import dataclass
from typing import List
import numpy as np
from numpy.typing import NDArray

from .ranking_types import RankingCase, RankingFunction
from .encoder import comparison_to_inequalities, expr_to_linear, Inequality


@dataclass
class RankingCaseEncoding:
    """Encoding of one ranking function case j for state q.

    Represents:
        [] A_j x ≤ b_j -> C_j x + d_j
    """
    A_j: NDArray[np.int64]  # Guard matrix: A_j x <= b_j
    b_j: NDArray[np.int64]  # Guard vector
    C_j: NDArray[np.int64]  # Expression row vector: C_j x + d_j
    d_j: int                # Expression constant (scalar)


@dataclass
class RankingFunctionEncoding:
    """Complete encoding of ranking function for one state.

    Contains encodings for all cases of V(x, q).
    Cases are ordered (first-match semantics).
    """
    state: str
    variables: List[str]              # Variable ordering
    cases: List[RankingCaseEncoding]  # One per case j

    def __repr__(self) -> str:
        lines = [f"Ranking function for state {self.state}"]
        lines.append(f"Variables: {self.variables}")
        lines.append(f"Number of cases: {len(self.cases)}")
        return "\n".join(lines)


def encode_ranking_case(
    case: RankingCase,
    variables: List[str]
) -> RankingCaseEncoding:
    """Encode one ranking case to (A_j, b_j, C_j, d_j).

    Args:
        case: The ranking case to encode
        variables: Ordered list of variables

    Returns:
        RankingCaseEncoding with matrices and vectors

    Raises:
        ValueError: If expression is not linear
    """
    # 1. Encode guards to (A_j, b_j)
    # Guards are just inequalities (no assignments), so we encode them
    # the same way we encode guard comparisons in transitions
    guard_ineqs: List[Inequality] = []
    for guard in case.guards:
        # primed=False because ranking function guards use current state variables only
        guard_ineqs.extend(comparison_to_inequalities(guard, primed=False))

    # Build variable index
    var_idx = {v: i for i, v in enumerate(variables)}
    n_vars = len(variables)

    # Build guard matrix A_j and vector b_j
    if guard_ineqs:
        m = len(guard_ineqs)
        A_j = np.zeros((m, n_vars), dtype=np.int64)
        b_j = np.zeros(m, dtype=np.int64)

        for i, ineq in enumerate(guard_ineqs):
            # All inequalities should be non-strict (≤) for ranking guards
            # If strict inequalities appear, we could convert them, but for now
            # we'll just encode them as-is (the comparison_to_inequalities handles this)
            for v, coeff in ineq.coeffs.items():
                if v in var_idx:
                    A_j[i, var_idx[v]] = coeff
            b_j[i] = ineq.const
    else:
        # No guards means always true (empty constraint)
        A_j = np.zeros((0, n_vars), dtype=np.int64)
        b_j = np.zeros(0, dtype=np.int64)

    # 2. Encode expression to (C_j, d_j)
    # Expression is a linear combination of variables + constant
    expr_lin = expr_to_linear(case.expression)

    # C_j is row vector of coefficients
    C_j = np.array([expr_lin.coeffs.get(v, 0) for v in variables], dtype=np.int64)

    # d_j is the constant term
    d_j = expr_lin.const

    return RankingCaseEncoding(A_j=A_j, b_j=b_j, C_j=C_j, d_j=d_j)


def encode_ranking_function(
    rf: RankingFunction,
    variables: List[str] | None = None
) -> RankingFunctionEncoding:
    """Encode all cases of a ranking function.

    Args:
        rf: The ranking function to encode
        variables: Optional ordered list of variables. If None, extracted from ranking function.

    Returns:
        RankingFunctionEncoding with all case encodings
    """
    # Get variables
    if variables is None:
        variables = sorted(rf.get_variables())

    # Encode each case
    case_encodings = [encode_ranking_case(case, variables) for case in rf.cases]

    return RankingFunctionEncoding(
        state=rf.state,
        variables=variables,
        cases=case_encodings
    )


def encode_ranking_functions(
    ranking_functions: dict[str, RankingFunction]
) -> dict[str, RankingFunctionEncoding]:
    """Encode multiple ranking functions with consistent variable ordering.

    Args:
        ranking_functions: Dict mapping state names to RankingFunction objects

    Returns:
        Dict mapping state names to RankingFunctionEncoding objects
    """
    # Collect all variables from all ranking functions
    all_vars: set[str] = set()
    for rf in ranking_functions.values():
        all_vars.update(rf.get_variables())

    variables = sorted(all_vars)

    # Encode each ranking function with the same variable ordering
    return {
        state: encode_ranking_function(rf, variables)
        for state, rf in ranking_functions.items()
    }
