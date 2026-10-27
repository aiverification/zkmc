"""Unit tests for Z3 SMT solver interface."""

import numpy as np
import pytest

from zkterm_tool.farkas import FarkasDual, build_farkas_dual
from zkterm_tool.z3_solver import BIG_M, solve_farkas_dual


def test_basic_sat_case():
    """
    Test SAT case: x = 0 ∧ x ≥ 0 ⟹ 10 - x > 0

    This should be SAT (always true when x = 0).
    """
    # Premise: x = 0 (x ≤ 0 ∧ -x ≤ 0)
    A_s = np.array([[1], [-1]], dtype=np.int64)
    b_s = np.array([0, 0], dtype=np.int64)

    # Conclusion: [x ≥ 0 ∧ 10 - x > 0]
    # Merged: [-x ≤ 0; -x > -10]
    C_p = np.array([
        [-1],  # x ≥ 0 becomes -x ≤ 0
        [-1],  # 10 - x > 0 becomes -x > -10
    ], dtype=np.int64)
    d_p = np.array([0, -10], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, C_p, d_p)
    sat, witness = solve_farkas_dual(dual)

    assert sat is True
    assert witness is not None
    assert "lambda_s_0" in witness
    assert "lambda_s_1" in witness
    assert "mu_s_0" in witness  # From merged A_p
    assert "mu_s_1" in witness  # From original C_p


def test_basic_unsat_case():
    """
    Test UNSAT case: x = 5 ⟹ x > 10

    This should be UNSAT (false - when x = 5, x is not > 10).
    """
    # Premise: x = 5 (x ≤ 5 ∧ -x ≤ -5)
    A_s = np.array([[1], [-1]], dtype=np.int64)
    b_s = np.array([5, -5], dtype=np.int64)

    # Conclusion: x > 10
    C_p = np.array([[1]], dtype=np.int64)
    d_p = np.array([10], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, C_p, d_p)
    sat, witness = solve_farkas_dual(dual)

    # This should be UNSAT because the implication is false
    assert sat is False
    assert witness is None


def test_multiple_variables():
    """
    Test with multiple variables: x = 0 ∧ y = 0 ⟹ x + y > -1

    This should be SAT (always true).
    """
    # Premise: x = 0 ∧ y = 0
    A_s = np.array([
        [1, 0],   # x ≤ 0
        [-1, 0],  # -x ≤ 0
        [0, 1],   # y ≤ 0
        [0, -1],  # -y ≤ 0
    ], dtype=np.int64)
    b_s = np.array([0, 0, 0, 0], dtype=np.int64)

    # Conclusion: x + y > -1
    C_p = np.array([[1, 1]], dtype=np.int64)
    d_p = np.array([-1], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, C_p, d_p)
    sat, witness = solve_farkas_dual(dual)

    assert sat is True
    assert witness is not None
    # Should have 4 lambda_s multipliers and 1 mu_p multiplier
    assert "lambda_s_0" in witness
    assert "lambda_s_1" in witness
    assert "lambda_s_2" in witness
    assert "lambda_s_3" in witness
    assert "mu_s_0" in witness


def test_with_additional_premise():
    """
    Test with merged conclusion: x ≤ 5 ⟹ [x ≥ 0 ∧ x > -1]

    This should be SAT (always true).
    """
    A_s = np.array([[1]], dtype=np.int64)
    b_s = np.array([5], dtype=np.int64)

    # Merged conclusion: [x ≥ 0; x > -1]
    C_p = np.array([
        [-1],  # x ≥ 0 becomes -x ≤ 0
        [1],   # x > -1
    ], dtype=np.int64)
    d_p = np.array([0, -1], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, C_p, d_p)
    sat, witness = solve_farkas_dual(dual)

    assert sat is True
    assert witness is not None
    assert "lambda_s_0" in witness
    assert "mu_s_0" in witness  # From merged A_p
    assert "mu_s_1" in witness  # From original C_p


def test_witness_values_nonnegative():
    """Test that witness values respect non-negativity constraints."""
    # SAT case: x = 0 ⟹ -x > -10
    A_s = np.array([[1], [-1]], dtype=np.int64)
    b_s = np.array([0, 0], dtype=np.int64)
    C_p = np.array([[-1]], dtype=np.int64)
    d_p = np.array([-10], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, C_p, d_p)
    sat, witness = solve_farkas_dual(dual)

    assert sat is True
    assert witness is not None

    # All multipliers should be non-negative
    for key, value in witness.items():
        assert value >= 0, f"{key} = {value} is negative"


def test_bounded_by_max_value():
    """Test that multipliers respect max_value bound."""
    # SAT case: x = 0 ⟹ -x > -10
    A_s = np.array([[1], [-1]], dtype=np.int64)
    b_s = np.array([0, 0], dtype=np.int64)
    C_p = np.array([[-1]], dtype=np.int64)
    d_p = np.array([-10], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, C_p, d_p)

    max_val = 1000
    sat, witness = solve_farkas_dual(dual, max_value=max_val)

    assert sat is True
    assert witness is not None

    # All multipliers should be ≤ max_value
    for key, value in witness.items():
        assert value <= max_val, f"{key} = {value} > {max_val}"


def test_multipliers_bounded_by_big_m():
    """
    Test that the default bound is the provers' range-check bound:
    2^31 · x ≤ -1 ⟹ x < 0

    Valid, but every certificate has μ_s = 2^31 · λ_s > BIG_M.
    """
    A_s = np.array([[BIG_M + 1]], dtype=np.int64)
    b_s = np.array([-1], dtype=np.int64)
    C_p = np.array([[-1]], dtype=np.int64)
    d_p = np.array([0], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, C_p, d_p)

    assert solve_farkas_dual(dual) == (False, None)
    sat, witness = solve_farkas_dual(dual, max_value=2**32)
    assert sat is True
    assert witness["mu_s_0"] == (BIG_M + 1) * witness["lambda_s_0"]


def test_constant_term_bounded_by_big_m():
    """
    Test that v = -(b_s^T λ_s + h_p^T μ_s) - 1 is bounded by BIG_M:
    x ≤ -2^32 ⟹ x < 0

    Valid, but every certificate has v = 2^32 · λ_s - 1 > BIG_M.
    """
    A_s = np.array([[1]], dtype=np.int64)
    b_s = np.array([-(2**32)], dtype=np.int64)
    C_p = np.array([[-1]], dtype=np.int64)
    d_p = np.array([0], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, C_p, d_p)

    assert solve_farkas_dual(dual) == (False, None)
    sat, witness = solve_farkas_dual(dual, max_value=2**33)
    assert sat is True
    assert 2**32 * witness["lambda_s_0"] - 1 <= 2**33


def test_zero_constant_terms_unsat():
    """
    Test UNSAT case with all-zero constant terms: x ≤ 0 ⟹ x > 0

    x = 0 is a counterexample; the zero witness must not be accepted.
    """
    A_s = np.array([[1]], dtype=np.int64)
    b_s = np.array([0], dtype=np.int64)
    C_p = np.array([[1]], dtype=np.int64)
    d_p = np.array([0], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, C_p, d_p)
    sat, witness = solve_farkas_dual(dual)

    assert sat is False
    assert witness is None


def test_ranking_well_definedness():
    """
    Test a realistic ranking function well-definedness check:
    x ≥ 0 ∧ x < 10 ⟹ 10 - x > 0

    This verifies that the ranking value is positive when the guard is satisfied.
    """
    # Premise: x ≥ 0 ∧ x < 10 (i.e., -x ≤ 0 ∧ x ≤ 9)
    A_s = np.array([
        [-1],  # -x ≤ 0
        [1],   # x ≤ 9
    ], dtype=np.int64)
    b_s = np.array([0, 9], dtype=np.int64)

    # Conclusion: 10 - x > 0, i.e., -x > -10
    C_p = np.array([[-1]], dtype=np.int64)
    d_p = np.array([-10], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, C_p, d_p)
    sat, witness = solve_farkas_dual(dual)

    assert sat is True
    assert witness is not None
