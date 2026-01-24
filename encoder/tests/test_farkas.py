"""Unit tests for Farkas lemma dual construction."""

import numpy as np
import pytest

from zkterm_tool.farkas import build_farkas_dual, build_farkas_dual_simple


def test_simple_implication():
    """
    Test: x = 0 ⟹ 10 - x > 0

    This checks that when x = 0, the value 10 - x is positive.
    """
    # Premise: x ≤ 0 ∧ -x ≤ 0 (i.e., x = 0)
    A_s = np.array([
        [1],   # x ≤ 0
        [-1],  # -x ≤ 0
    ], dtype=np.int64)
    b_s = np.array([0, 0], dtype=np.int64)

    # No additional premise
    A_p = np.zeros((0, 1), dtype=np.int64)
    b_p = np.zeros(0, dtype=np.int64)

    # Conclusion: 10 - x > 0, i.e., -x > -10
    C_p = np.array([[-1]], dtype=np.int64)
    d_p = np.array([-10], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, A_p, b_p, C_p, d_p)

    # Check dimensions
    assert dual.n_vars == 1
    assert dual.A_eq.shape == (1, 3)  # 1 variable, 3 multipliers (2 λ_s, 1 μ_p)

    # Check indices
    assert len(dual.lambda_s_indices) == 2
    assert len(dual.lambda_p_indices) == 0
    assert len(dual.mu_p_indices) == 1


def test_2d_implication_with_premises():
    """
    Test: x + y ≤ 10 ∧ x ≥ 0 ⟹ y ≤ 11 (i.e., y > 10 is false)
    """
    # Premise: x + y ≤ 10, x ≥ 0 (i.e., -x ≤ 0)
    A_s = np.array([
        [1, 1],   # x + y ≤ 10
        [-1, 0],  # -x ≤ 0
    ], dtype=np.int64)
    b_s = np.array([10, 0], dtype=np.int64)

    # No additional premise
    A_p = np.zeros((0, 2), dtype=np.int64)
    b_p = np.zeros(0, dtype=np.int64)

    # Conclusion: y > 10, i.e., [0, 1] y > 10
    C_p = np.array([[0, 1]], dtype=np.int64)
    d_p = np.array([10], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, A_p, b_p, C_p, d_p)

    # Check dimensions
    assert dual.n_vars == 2
    assert dual.A_eq.shape == (2, 3)  # 2 variables, 3 multipliers (2 λ_s, 1 μ_p)

    # Check that we have 2 lambda_s, 0 lambda_p, 1 mu_p
    assert len(dual.lambda_s_indices) == 2
    assert len(dual.lambda_p_indices) == 0
    assert len(dual.mu_p_indices) == 1


def test_simple_wrapper():
    """Test build_farkas_dual_simple wrapper."""
    # Simple: x = 0 ⟹ -x > -10
    A_s = np.array([[1], [-1]], dtype=np.int64)
    b_s = np.array([0, 0], dtype=np.int64)
    C_p = np.array([[-1]], dtype=np.int64)
    d_p = np.array([-10], dtype=np.int64)

    dual = build_farkas_dual_simple(A_s, b_s, C_p, d_p)

    # Should be equivalent to calling build_farkas_dual with empty A_p, b_p
    assert dual.n_vars == 1
    assert len(dual.lambda_p_indices) == 0
    assert len(dual.lambda_s_indices) == 2
    assert len(dual.mu_p_indices) == 1


def test_empty_premise():
    """Test with empty premise."""
    # Empty premise ⟹ x > 5
    A_s = np.zeros((0, 1), dtype=np.int64)
    b_s = np.zeros(0, dtype=np.int64)
    A_p = np.zeros((0, 1), dtype=np.int64)
    b_p = np.zeros(0, dtype=np.int64)

    # Conclusion: x > 5
    C_p = np.array([[1]], dtype=np.int64)
    d_p = np.array([5], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, A_p, b_p, C_p, d_p)

    # Check dimensions
    assert dual.n_vars == 1
    assert len(dual.lambda_s_indices) == 0
    assert len(dual.mu_p_indices) == 1


def test_additional_premise():
    """Test with both A_s and A_p premises."""
    # A_s: x ≤ 5
    A_s = np.array([[1]], dtype=np.int64)
    b_s = np.array([5], dtype=np.int64)

    # A_p: x ≥ 0 (i.e., -x ≤ 0)
    A_p = np.array([[-1]], dtype=np.int64)
    b_p = np.array([0], dtype=np.int64)

    # Conclusion: x > -1
    C_p = np.array([[1]], dtype=np.int64)
    d_p = np.array([-1], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, A_p, b_p, C_p, d_p)

    # Check we have all three types of multipliers
    assert len(dual.lambda_s_indices) == 1
    assert len(dual.lambda_p_indices) == 1
    assert len(dual.mu_p_indices) == 1

    # Total 3 multipliers
    total = len(dual.lambda_s_indices) + len(dual.lambda_p_indices) + len(dual.mu_p_indices)
    assert dual.A_eq.shape[1] == total


def test_const_coeffs():
    """Test that constant coefficients are computed correctly."""
    # x ≤ 5 ⟹ x > 2
    A_s = np.array([[1]], dtype=np.int64)
    b_s = np.array([5], dtype=np.int64)
    A_p = np.zeros((0, 1), dtype=np.int64)
    b_p = np.zeros(0, dtype=np.int64)
    C_p = np.array([[1]], dtype=np.int64)
    d_p = np.array([2], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, A_p, b_p, C_p, d_p)

    # const_coeffs should be [b_s; b_p; d_p] = [5; 2]
    expected = np.array([5, 2], dtype=np.int64)
    np.testing.assert_array_equal(dual.const_coeffs, expected)
