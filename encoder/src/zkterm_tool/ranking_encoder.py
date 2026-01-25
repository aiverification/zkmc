"""Encode ranking functions as matrix/vector forms.

Given a ranking function V(x, q) as piecewise linear cases:
    rank(q):
        [] guard_1 -> expression_1
        [] guard_2 -> expression_2
        ...

We encode each case j as:
    V(x, q) = W_j x + u_j  if  C_j x ≤ d_j

Where:
    - C_j is a matrix (guard can have multiple inequalities)
    - d_j is a vector
    - W_j is a row vector (expression returns scalar)
    - u_j is a scalar
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

    Paper notation: V(x, q) = W_j x + u_j  if  C_j x ≤ d_j

    Represents:
        [] C_j x ≤ d_j -> W_j x + u_j
    """
    C_j: NDArray[np.int64]  # Guard matrix: C_j x ≤ d_j
    d_j: NDArray[np.int64]  # Guard vector
    W_j: NDArray[np.int64]  # Expression row vector: W_j x + u_j
    u_j: int                # Expression constant (scalar)


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
    """Encode one ranking case to (C_j, d_j, W_j, u_j).

    Paper notation: V(x, q) = W_j x + u_j  if  C_j x ≤ d_j

    Args:
        case: The ranking case to encode
        variables: Ordered list of variables

    Returns:
        RankingCaseEncoding with matrices and vectors

    Raises:
        ValueError: If expression is not linear
    """
    # 1. Encode guards to (C_j, d_j)
    # Guards are just inequalities (no assignments), so we encode them
    # the same way we encode guard comparisons in transitions
    guard_ineqs: List[Inequality] = []
    for guard in case.guards:
        # primed=False because ranking function guards use current state variables only
        guard_ineqs.extend(comparison_to_inequalities(guard, primed=False))

    # Build variable index
    var_idx = {v: i for i, v in enumerate(variables)}
    n_vars = len(variables)

    # Build guard matrix C_j and vector d_j
    if guard_ineqs:
        m = len(guard_ineqs)
        C_j = np.zeros((m, n_vars), dtype=np.int64)
        d_j = np.zeros(m, dtype=np.int64)

        for i, ineq in enumerate(guard_ineqs):
            # All inequalities should be non-strict (≤) for ranking guards
            # If strict inequalities appear, we could convert them, but for now
            # we'll just encode them as-is (the comparison_to_inequalities handles this)
            for v, coeff in ineq.coeffs.items():
                if v in var_idx:
                    C_j[i, var_idx[v]] = coeff
            d_j[i] = ineq.const
    else:
        # No guards means always true (empty constraint)
        C_j = np.zeros((0, n_vars), dtype=np.int64)
        d_j = np.zeros(0, dtype=np.int64)

    # 2. Encode expression to (W_j, u_j)
    # Expression is a linear combination of variables + constant
    expr_lin = expr_to_linear(case.expression)

    # W_j is row vector of coefficients
    W_j = np.array([expr_lin.coeffs.get(v, 0) for v in variables], dtype=np.int64)

    # u_j is the constant term
    u_j = expr_lin.const

    return RankingCaseEncoding(C_j=C_j, d_j=d_j, W_j=W_j, u_j=u_j)


def encode_ranking_function(
    rf: RankingFunction,
    variables: List[str] | None = None
) -> RankingFunctionEncoding:
    """Encode all cases of a ranking function.

    Args:
        rf: The ranking function to encode
        variables: Optional ordered list of variables. If None, extracted from ranking function only.
                   IMPORTANT: When verifying against program transitions, provide the full program
                   variable set to ensure matrix dimensions align correctly. Guards mentioning only
                   a subset of variables will have coefficient 0 for unmentioned variables (meaning
                   those variables are unconstrained).

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
