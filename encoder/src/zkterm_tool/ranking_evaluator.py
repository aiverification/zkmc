"""Ranking function evaluation on concrete states.

This module provides functionality to evaluate ranking functions on concrete
states for explicit-state verification. It handles guard checking and ranking
value computation.
"""

from typing import Dict, Optional
import numpy as np
from .ranking_encoder import RankingFunctionEncoding


def check_guard(
    state_vec: np.ndarray,
    C_j: np.ndarray,
    d_j: np.ndarray
) -> bool:
    """Check if guard C_j x ≤ d_j is satisfied.

    Args:
        state_vec: State as vector (ordered by variables)
        C_j: Guard matrix (m, n) where m is number of constraints
        d_j: Guard vector (m,) with constraint bounds

    Returns:
        True if all constraints satisfied, False otherwise

    Note:
        Empty guards (C_j with 0 rows) are treated as always true,
        representing unconditional ranking function cases ([] true -> expr).

    Example:
        >>> state_vec = np.array([5, 3])
        >>> C_j = np.array([[1, 0], [-1, 0]])  # x >= 0 && x <= 10
        >>> d_j = np.array([10, 0])
        >>> check_guard(state_vec, C_j, d_j)
        True
    """
    if C_j.shape[0] == 0:
        # Empty guard (always true, e.g., [] true -> expr)
        return True

    # Compute C_j @ x
    result = C_j @ state_vec

    # Check all constraints: result ≤ d_j (element-wise)
    return bool(np.all(result <= d_j))


def evaluate_ranking(
    state_dict: Dict[str, int],
    rank_enc: RankingFunctionEncoding
) -> Optional[int]:
    """Evaluate ranking function V(s, q) for a concrete state.

    Uses first-match semantics across finite and infinity cases:
    1. Checks finite cases in order - returns value if guard satisfied
    2. Checks infinity cases in order - returns None (+∞) if guard satisfied
    3. If no case matches, this indicates a coverage gap (validation should catch this)

    Args:
        state_dict: State as dictionary {'var': value, ...}
        rank_enc: Ranking function encoding for automaton state q

    Returns:
        Ranking value (int) if a finite case guard is satisfied
        None if an infinity case guard is satisfied (V(s,q) = +∞)
        None if no guard satisfied (validation should prevent this)

    Note:
        First-match semantics: Returns value from first satisfied case.
        This is consistent with the verifier's behavior.

    Example:
        >>> state = {'x': 5, 'y': 3}
        >>> value = evaluate_ranking(state, rank_enc)
        >>> # Returns int if finite case matched, None if infinity case matched
    """
    # Convert state dict to vector in correct order
    state_vec = np.array(
        [state_dict[var] for var in rank_enc.variables],
        dtype=np.int64
    )

    # First, check finite cases in order (first-match semantics)
    for case in rank_enc.finite_cases:
        if check_guard(state_vec, case.C_j, case.d_j):
            # Guard satisfied: compute V = w_j · x + u_j
            value = np.dot(case.w_j, state_vec) + case.u_j
            return int(value)

    # No finite case satisfied: check infinity cases
    for case in rank_enc.infinity_cases:
        if check_guard(state_vec, case.E_k, case.f_k):
            # Infinity case satisfied: V(s,q) = +∞
            return None

    # No case satisfied at all (should be caught by validation - coverage check)
    # Return None to indicate undefined ranking
    return None


def check_automaton_guard(
    state_dict: Dict[str, int],
    aut_enc
) -> bool:
    """Check if automaton transition guard is satisfied.

    Args:
        state_dict: State as dictionary {'var': value, ...}
        aut_enc: AutomatonTransitionEncoding with guard matrices P and r

    Returns:
        True if guard P x ≤ r is satisfied

    Example:
        >>> state = {'x': 5, 'y': 3}
        >>> enabled = check_automaton_guard(state, aut_enc)
        >>> # Returns True if transition is enabled at this state
    """
    # Convert to vector in correct order
    state_vec = np.array(
        [state_dict[var] for var in aut_enc.variables],
        dtype=np.int64
    )

    # Check guard P x ≤ r
    return check_guard(state_vec, aut_enc.P, aut_enc.r)
