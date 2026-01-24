"""Farkas lemma dual construction for verification obligations.

This module implements the Farkas lemma transformation to convert
implication checking into a satisfiability problem solvable by SMT solvers.

Farkas Lemma:
    ∀y: A_s y ≤ b_s ⟹ A_p y ≤ b_p ⟹ C_p y > d_p

Is equivalent to:
    ¬∃y: A_s y ≤ b_s ∧ A_p y ≤ b_p ∧ C_p y ≤ d_p

Which holds iff there exist λ_s ≥ 0, λ_p ≥ 0, μ_p ≥ 0 such that:
    1. A_s^T λ_s + A_p^T λ_p + C_p^T μ_p = 0  (dual equality)
    2. b_s^T λ_s + b_p^T λ_p + d_p^T μ_p < 0   (constant term negative)

For integer semantics, we convert the strict inequality to:
    b_s^T λ_s + b_p^T λ_p + d_p^T μ_p ≤ -1
"""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass
class FarkasDual:
    """Farkas dual formulation of an implication.

    Given: ∀y: A_s y ≤ b_s ⟹ A_p y ≤ b_p ⟹ C_p y > d_p

    The dual formulation requires finding λ_s, λ_p, μ_p such that:
    - n_vars: Number of variables in the original problem
    - n_lambda_s: Number of λ_s multipliers (equals number of rows in A_s)
    - n_lambda_p: Number of λ_p multipliers (equals number of rows in A_p)
    - n_mu_p: Number of μ_p multipliers (equals number of rows in C_p)

    Attributes:
        n_vars: Number of variables in original problem (dimension of y)
        A_eq: Equality constraint matrix (n_vars × total_multipliers)
        b_eq: Equality constraint RHS (should equal 0)
        const_coeffs: Coefficients for constant inequality constraint
        lambda_s_indices: Indices in multiplier vector for λ_s (must be ≥ 0)
        lambda_p_indices: Indices for λ_p (must be ≥ 0)
        mu_p_indices: Indices for μ_p (must be ≥ 0)
    """
    n_vars: int
    A_eq: NDArray[np.int64]          # Equality constraints
    b_eq: NDArray[np.int64]          # Should be 0
    const_coeffs: NDArray[np.int64]  # Coefficients for b_s^T λ_s + ...
    lambda_s_indices: list[int]      # Which vars are λ_s (≥ 0)
    lambda_p_indices: list[int]      # Which vars are λ_p (≥ 0)
    mu_p_indices: list[int]          # Which vars are μ_p (≥ 0)


def build_farkas_dual(
    A_s: NDArray[np.int64],
    b_s: NDArray[np.int64],
    A_p: NDArray[np.int64],
    b_p: NDArray[np.int64],
    C_p: NDArray[np.int64],
    d_p: NDArray[np.int64],
) -> FarkasDual:
    """Construct Farkas dual for implication.

    Given: ∀y: A_s y ≤ b_s ⟹ A_p y ≤ b_p ⟹ C_p y > d_p

    Constructs the dual formulation that can be solved by an SMT solver.

    Args:
        A_s: Premise matrix (m_s × n)
        b_s: Premise vector (m_s,)
        A_p: Additional premise matrix (m_p × n)
        b_p: Additional premise vector (m_p,)
        C_p: Conclusion matrix (m_c × n) for strict inequality
        d_p: Conclusion vector (m_c,) for strict inequality

    Returns:
        FarkasDual object containing the dual formulation

    The dual requires finding λ_s, λ_p, μ_p such that:
        A_s^T λ_s + A_p^T λ_p + C_p^T μ_p = 0
        b_s^T λ_s + b_p^T λ_p + d_p^T μ_p ≤ -1
        λ_s ≥ 0, λ_p ≥ 0, μ_p ≥ 0
    """
    # Get dimensions
    m_s, n = A_s.shape if A_s.size > 0 else (0, A_p.shape[1] if A_p.size > 0 else C_p.shape[1])
    m_p = A_p.shape[0] if A_p.size > 0 else 0
    m_c = C_p.shape[0] if C_p.size > 0 else 0

    # If A_s is empty, infer n from A_p or C_p
    if m_s == 0:
        n = A_p.shape[1] if m_p > 0 else C_p.shape[1]

    # Total number of multipliers: λ_s (m_s) + λ_p (m_p) + μ_p (m_c)
    total_multipliers = m_s + m_p + m_c

    # Index ranges for each set of multipliers
    lambda_s_indices = list(range(0, m_s))
    lambda_p_indices = list(range(m_s, m_s + m_p))
    mu_p_indices = list(range(m_s + m_p, total_multipliers))

    # Build equality constraint: A_s^T λ_s + A_p^T λ_p + C_p^T μ_p = 0
    # This is an n × total_multipliers matrix
    A_eq = np.zeros((n, total_multipliers), dtype=np.int64)

    # Fill in A_s^T contribution
    if m_s > 0:
        A_eq[:, lambda_s_indices] = A_s.T

    # Fill in A_p^T contribution
    if m_p > 0:
        A_eq[:, lambda_p_indices] = A_p.T

    # Fill in C_p^T contribution
    # To check A_s y ≤ b_s ∧ A_p y ≤ b_p ⟹ C_p y > d_p, we verify:
    # ¬∃y: A_s y ≤ b_s ∧ A_p y ≤ b_p ∧ C_p y ≤ d_p
    # Farkas dual: A_s^T λ_s + A_p^T λ_p + C_p^T μ_p = 0
    if m_c > 0:
        A_eq[:, mu_p_indices] = C_p.T

    b_eq = np.zeros(n, dtype=np.int64)

    # Build constant inequality coefficient vector: [b_s^T, b_p^T, d_p^T]
    # Farkas dual constant inequality:
    # b_s^T λ_s + b_p^T λ_p + d_p^T μ_p < 0
    # For integer semantics: b_s^T λ_s + b_p^T λ_p + d_p^T μ_p ≤ -1

    const_coeffs = np.zeros(total_multipliers, dtype=np.int64)

    if m_s > 0:
        const_coeffs[lambda_s_indices] = b_s

    if m_p > 0:
        const_coeffs[lambda_p_indices] = b_p

    if m_c > 0:
        # Coefficient for μ_p in constant inequality
        const_coeffs[mu_p_indices] = d_p

    return FarkasDual(
        n_vars=n,
        A_eq=A_eq,
        b_eq=b_eq,
        const_coeffs=const_coeffs,
        lambda_s_indices=lambda_s_indices,
        lambda_p_indices=lambda_p_indices,
        mu_p_indices=mu_p_indices,
    )


def build_farkas_dual_simple(
    A_s: NDArray[np.int64],
    b_s: NDArray[np.int64],
    C_p: NDArray[np.int64],
    d_p: NDArray[np.int64],
) -> FarkasDual:
    """Simplified version without additional premises A_p.

    Given: ∀y: A_s y ≤ b_s ⟹ C_p y > d_p

    This is equivalent to build_farkas_dual with empty A_p and b_p.

    Args:
        A_s: Premise matrix
        b_s: Premise vector
        C_p: Conclusion matrix (strict inequality)
        d_p: Conclusion vector (strict inequality)

    Returns:
        FarkasDual object
    """
    # Get n from A_s or C_p
    n = A_s.shape[1] if A_s.size > 0 else C_p.shape[1]

    # Create empty A_p and b_p
    A_p = np.zeros((0, n), dtype=np.int64)
    b_p = np.zeros(0, dtype=np.int64)

    return build_farkas_dual(A_s, b_s, A_p, b_p, C_p, d_p)
