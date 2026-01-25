"""Encode ranking functions as matrix/vector forms.

Given a ranking function V(x, q) with finite and infinity cases:
    rank(q):
        [] guard_1 -> expression_1  (finite)
        [] guard_2 -> expression_2  (finite)
        [] guard_3 -> inf           (infinity)
        ...

We encode finite cases j as:
    V(x, q) = w_j x + u_j  if  C_j x ≤ d_j

And infinity cases k as:
    V(x, q) = +∞  if  E_k x ≤ f_k

Where:
    - C_j, E_k are matrices (guards can have multiple inequalities)
    - d_j, f_k are vectors
    - w_j is a row vector (expression returns scalar, not matrix)
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
    """Encoding of one finite ranking function case j for state q.

    Paper notation: V(x, q) = w_j x + u_j  if  C_j x ≤ d_j

    Represents:
        [] C_j x ≤ d_j -> w_j x + u_j

    Note: w_j is a vector, not a matrix (hence lowercase).
    """
    C_j: NDArray[np.int64]  # Guard matrix: C_j x ≤ d_j
    d_j: NDArray[np.int64]  # Guard vector
    w_j: NDArray[np.int64]  # Expression row vector: w_j x + u_j
    u_j: int                # Expression constant (scalar)


@dataclass
class InfinityCaseEncoding:
    """Encoding of one infinity ranking function case k for state q.

    Paper notation: V(x, q) = +∞  if  E_k x ≤ f_k

    Represents:
        [] E_k x ≤ f_k -> inf
    """
    E_k: NDArray[np.int64]  # Guard matrix: E_k x ≤ f_k
    f_k: NDArray[np.int64]  # Guard vector


@dataclass
class RankingFunctionEncoding:
    """Complete encoding of ranking function for one state.

    Contains separate encodings for finite and infinity cases of V(x, q).
    Finite cases: j = 1...m compute w_j x + u_j
    Infinity cases: k = 1...l assign +∞
    """
    state: str
    variables: List[str]                          # Variable ordering
    finite_cases: List[RankingCaseEncoding]       # Finite cases (j=1...m)
    infinity_cases: List[InfinityCaseEncoding]    # Infinity cases (k=1...l)

    def __repr__(self) -> str:
        lines = [f"Ranking function for state {self.state}"]
        lines.append(f"Variables: {self.variables}")
        lines.append(f"Number of finite cases: {len(self.finite_cases)}")
        lines.append(f"Number of infinity cases: {len(self.infinity_cases)}")
        return "\n".join(lines)


def encode_ranking_case(
    case: RankingCase,
    variables: List[str]
) -> RankingCaseEncoding:
    """Encode one finite ranking case to (C_j, d_j, w_j, u_j).

    Paper notation: V(x, q) = w_j x + u_j  if  C_j x ≤ d_j

    Args:
        case: The finite ranking case to encode (must have is_infinity=False)
        variables: Ordered list of variables

    Returns:
        RankingCaseEncoding with matrices and vectors

    Raises:
        ValueError: If expression is not linear or case is an infinity case
    """
    if case.is_infinity:
        raise ValueError("Cannot encode infinity case as finite case. Use encode_infinity_case instead.")

    # 1. Encode guards to (C_j, d_j)
    # Guards are just inequalities (no assignments), so we encode them
    # the same way we encode guard comparisons in transitions
    guard_ineqs: List[Inequality] = []
    for guard in case.guards:
        # primed=False because ranking function guards use current state variables only
        guard_ineqs.extend(comparison_to_inequalities(guard, primed=False))

    # Convert strict inequalities to non-strict using integer semantics
    # x < c becomes x <= c-1, x > c becomes x >= c+1 (encoded as -x <= -c-1)
    guard_ineqs = [ineq.to_nonstrict() for ineq in guard_ineqs]

    # Build variable index
    var_idx = {v: i for i, v in enumerate(variables)}
    n_vars = len(variables)

    # Build guard matrix C_j and vector d_j
    if guard_ineqs:
        m = len(guard_ineqs)
        C_j = np.zeros((m, n_vars), dtype=np.int64)
        d_j = np.zeros(m, dtype=np.int64)

        for i, ineq in enumerate(guard_ineqs):
            # All inequalities are now non-strict (converted above)
            for v, coeff in ineq.coeffs.items():
                if v in var_idx:
                    C_j[i, var_idx[v]] = coeff
            d_j[i] = ineq.const
    else:
        # No guards means always true (empty constraint)
        C_j = np.zeros((0, n_vars), dtype=np.int64)
        d_j = np.zeros(0, dtype=np.int64)

    # 2. Encode expression to (w_j, u_j)
    # Expression is a linear combination of variables + constant
    expr_lin = expr_to_linear(case.expression)

    # w_j is row vector of coefficients (lowercase: it's a vector, not a matrix)
    w_j = np.array([expr_lin.coeffs.get(v, 0) for v in variables], dtype=np.int64)

    # u_j is the constant term
    u_j = expr_lin.const

    return RankingCaseEncoding(C_j=C_j, d_j=d_j, w_j=w_j, u_j=u_j)


def encode_infinity_case(
    case: RankingCase,
    variables: List[str]
) -> InfinityCaseEncoding:
    """Encode one infinity ranking case to (E_k, f_k).

    Paper notation: V(x, q) = +∞  if  E_k x ≤ f_k

    Args:
        case: The infinity ranking case to encode (must have is_infinity=True)
        variables: Ordered list of variables

    Returns:
        InfinityCaseEncoding with guard matrix and vector

    Raises:
        ValueError: If case is not an infinity case
    """
    if not case.is_infinity:
        raise ValueError("Cannot encode finite case as infinity case. Use encode_ranking_case instead.")

    # Encode guards to (E_k, f_k)
    guard_ineqs: List[Inequality] = []
    for guard in case.guards:
        # primed=False because ranking function guards use current state variables only
        guard_ineqs.extend(comparison_to_inequalities(guard, primed=False))

    # Convert strict inequalities to non-strict using integer semantics
    guard_ineqs = [ineq.to_nonstrict() for ineq in guard_ineqs]

    # Build variable index
    var_idx = {v: i for i, v in enumerate(variables)}
    n_vars = len(variables)

    # Build guard matrix E_k and vector f_k
    if guard_ineqs:
        m = len(guard_ineqs)
        E_k = np.zeros((m, n_vars), dtype=np.int64)
        f_k = np.zeros(m, dtype=np.int64)

        for i, ineq in enumerate(guard_ineqs):
            for v, coeff in ineq.coeffs.items():
                if v in var_idx:
                    E_k[i, var_idx[v]] = coeff
            f_k[i] = ineq.const
    else:
        # No guards means always true (empty constraint)
        E_k = np.zeros((0, n_vars), dtype=np.int64)
        f_k = np.zeros(0, dtype=np.int64)

    return InfinityCaseEncoding(E_k=E_k, f_k=f_k)


def encode_ranking_function(
    rf: RankingFunction,
    variables: List[str] | None = None
) -> RankingFunctionEncoding:
    """Encode all cases of a ranking function, separating finite and infinity cases.

    Args:
        rf: The ranking function to encode
        variables: Optional ordered list of variables. If None, extracted from ranking function only.
                   IMPORTANT: When verifying against program transitions, provide the full program
                   variable set to ensure matrix dimensions align correctly. Guards mentioning only
                   a subset of variables will have coefficient 0 for unmentioned variables (meaning
                   those variables are unconstrained).

    Returns:
        RankingFunctionEncoding with separate finite and infinity case encodings
    """
    # Get variables
    if variables is None:
        variables = sorted(rf.get_variables())

    # Separate and encode finite vs infinity cases
    finite_case_encodings = []
    infinity_case_encodings = []

    for case in rf.cases:
        if case.is_infinity:
            infinity_case_encodings.append(encode_infinity_case(case, variables))
        else:
            finite_case_encodings.append(encode_ranking_case(case, variables))

    return RankingFunctionEncoding(
        state=rf.state,
        variables=variables,
        finite_cases=finite_case_encodings,
        infinity_cases=infinity_case_encodings
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
