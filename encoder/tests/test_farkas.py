"""Unit tests for Farkas lemma dual construction."""

import numpy as np
import pytest

from zkterm_tool.farkas import build_farkas_dual, build_farkas_dual_simple, build_farkas_dual_disjunctive


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

    # Conclusion: 10 - x > 0, i.e., -x > -10
    C_p = np.array([[-1]], dtype=np.int64)
    d_p = np.array([-10], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, C_p, d_p)

    # Check dimensions
    assert dual.n_vars == 1
    assert dual.A_eq.shape == (1, 3)  # 1 variable, 3 multipliers (2 λ_s, 1 μ_p)

    # Check indices (only λ_s and μ_p now)
    assert len(dual.lambda_s_indices) == 2
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

    # Conclusion: y > 10, i.e., [0, 1] y > 10
    C_p = np.array([[0, 1]], dtype=np.int64)
    d_p = np.array([10], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, C_p, d_p)

    # Check dimensions
    assert dual.n_vars == 2
    assert dual.A_eq.shape == (2, 3)  # 2 variables, 3 multipliers (2 λ_s, 1 μ_p)

    # Check that we have 2 lambda_s, 1 mu_p (no lambda_p)
    assert len(dual.lambda_s_indices) == 2
    assert len(dual.mu_p_indices) == 1


def test_simple_wrapper():
    """Test build_farkas_dual_simple wrapper."""
    # Simple: x = 0 ⟹ -x > -10
    A_s = np.array([[1], [-1]], dtype=np.int64)
    b_s = np.array([0, 0], dtype=np.int64)
    C_p = np.array([[-1]], dtype=np.int64)
    d_p = np.array([-10], dtype=np.int64)

    dual = build_farkas_dual_simple(A_s, b_s, C_p, d_p)

    # Should be equivalent to calling build_farkas_dual
    assert dual.n_vars == 1
    assert len(dual.lambda_s_indices) == 2
    assert len(dual.mu_p_indices) == 1


def test_empty_premise():
    """Test with empty premise."""
    # Empty premise ⟹ x > 5
    A_s = np.zeros((0, 1), dtype=np.int64)
    b_s = np.zeros(0, dtype=np.int64)

    # Conclusion: x > 5
    C_p = np.array([[1]], dtype=np.int64)
    d_p = np.array([5], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, C_p, d_p)

    # Check dimensions
    assert dual.n_vars == 1
    assert len(dual.lambda_s_indices) == 0
    assert len(dual.mu_p_indices) == 1


def test_additional_premise():
    """Test with middle premise merged into conclusion.

    Old formulation: A_s: x ≤ 5 ⟹ A_p: x ≥ 0 ⟹ C_p: x > -1
    New formulation: A_s: x ≤ 5 ⟹ C_p_new: [A_p; C_p] where:
        - Row 1 (from A_p): -x ≤ 0 (checked via negation: -x > 0 must be false)
        - Row 2 (from C_p): x > -1
    """
    # A_s: x ≤ 5
    A_s = np.array([[1]], dtype=np.int64)
    b_s = np.array([5], dtype=np.int64)

    # Merged conclusion: [A_p; C_p]
    # Row 1: x ≥ 0 becomes -x ≤ 0 in conclusion
    # Row 2: x > -1
    C_p_new = np.array([
        [-1],  # From A_p: -x ≤ 0
        [1],   # From C_p: x > -1
    ], dtype=np.int64)
    d_p_new = np.array([0, -1], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, C_p_new, d_p_new)

    # Check we have only λ_s and μ_p (no λ_p)
    assert len(dual.lambda_s_indices) == 1
    assert len(dual.mu_p_indices) == 2

    # Total 3 multipliers (1 λ_s, 2 μ_p)
    total = len(dual.lambda_s_indices) + len(dual.mu_p_indices)
    assert total == 3
    assert dual.A_eq.shape[1] == total


def test_const_coeffs():
    """Test that constant coefficients are computed correctly."""
    # x ≤ 5 ⟹ x > 2
    A_s = np.array([[1]], dtype=np.int64)
    b_s = np.array([5], dtype=np.int64)
    C_p = np.array([[1]], dtype=np.int64)
    d_p = np.array([2], dtype=np.int64)

    dual = build_farkas_dual(A_s, b_s, C_p, d_p)

    # const_coeffs should be [b_s; d_p] = [5; 2]
    expected = np.array([5, 2], dtype=np.int64)
    np.testing.assert_array_equal(dual.const_coeffs, expected)


class TestFarkasDualDisjunctive:
    """Tests for disjunctive Farkas dual construction."""

    def test_single_disjunct(self):
        """Test disjunctive with m=1 (single case).

        A_s y ≤ b_s ⟹ C y ≤ d ⟹ E_1 y > f_1

        Example: x ≤ 5 ⟹ ⊤ ⟹ x > 2
        """
        # Premise
        A_s = np.array([[1]], dtype=np.int64)
        b_s = np.array([5], dtype=np.int64)

        # Middle premise (empty - always true)
        C = np.zeros((0, 1), dtype=np.int64)
        d = np.zeros(0, dtype=np.int64)

        # Disjunctive conclusion (single case)
        E_list = [np.array([[1]], dtype=np.int64)]
        f_list = [np.array([2], dtype=np.int64)]

        dual = build_farkas_dual_disjunctive(A_s, b_s, C, d, E_list, f_list)

        # Should have λ_s (1 from A_s) and μ_p (1 from E_1)
        assert len(dual.lambda_s_indices) == 1
        assert len(dual.mu_p_indices) == 1
        assert dual.n_vars == 1
        assert dual.A_eq.shape == (1, 2)  # 1 variable, 2 multipliers

    def test_two_disjuncts(self):
        """Test disjunctive with m=2 (two cases).

        A_s y ≤ b_s ⟹ C y ≤ d ⟹ (E_1 y > f_1 ∨ E_2 y > f_2)

        Example: x ≤ 10 ⟹ x ≥ 0 ⟹ (x > 2 ∨ x > 5)
        """
        # Premise
        A_s = np.array([[1]], dtype=np.int64)
        b_s = np.array([10], dtype=np.int64)

        # Middle premise: x ≥ 0 (i.e., -x ≤ 0)
        C = np.array([[-1]], dtype=np.int64)
        d = np.array([0], dtype=np.int64)

        # Disjunctive conclusion (two cases)
        E_list = [
            np.array([[1]], dtype=np.int64),  # E_1: x > 2
            np.array([[1]], dtype=np.int64),  # E_2: x > 5
        ]
        f_list = [
            np.array([2], dtype=np.int64),    # f_1
            np.array([5], dtype=np.int64),    # f_2
        ]

        dual = build_farkas_dual_disjunctive(A_s, b_s, C, d, E_list, f_list)

        # Should have λ_s (1), and μ_p (1 from C + 1 from E_1 + 1 from E_2 = 3)
        assert len(dual.lambda_s_indices) == 1
        assert len(dual.mu_p_indices) == 3
        assert dual.n_vars == 1
        assert dual.A_eq.shape == (1, 4)  # 1 variable, 4 multipliers

    def test_three_disjuncts_2d(self):
        """Test disjunctive with m=3 in 2D space.

        Example: x + y ≤ 10 ⟹ ⊤ ⟹ (x > 0 ∨ y > 0 ∨ x + y > 1)
        """
        # Premise
        A_s = np.array([[1, 1]], dtype=np.int64)
        b_s = np.array([10], dtype=np.int64)

        # Middle premise (empty)
        C = np.zeros((0, 2), dtype=np.int64)
        d = np.zeros(0, dtype=np.int64)

        # Disjunctive conclusion (three cases)
        E_list = [
            np.array([[1, 0]], dtype=np.int64),  # E_1: x > 0
            np.array([[0, 1]], dtype=np.int64),  # E_2: y > 0
            np.array([[1, 1]], dtype=np.int64),  # E_3: x + y > 1
        ]
        f_list = [
            np.array([0], dtype=np.int64),  # f_1
            np.array([0], dtype=np.int64),  # f_2
            np.array([1], dtype=np.int64),  # f_3
        ]

        dual = build_farkas_dual_disjunctive(A_s, b_s, C, d, E_list, f_list)

        # Should have λ_s (1) and μ_p (3 from E_1, E_2, E_3)
        assert len(dual.lambda_s_indices) == 1
        assert len(dual.mu_p_indices) == 3
        assert dual.n_vars == 2
        assert dual.A_eq.shape == (2, 4)  # 2 variables, 4 multipliers

    def test_empty_middle_premise(self):
        """Test disjunctive with empty middle premise.

        A_s y ≤ b_s ⟹ ⊤ ⟹ ∨_k E_k y > f_k

        This is the pattern for initial obligations where C, d are empty.
        """
        # Premise: x ≤ 5 ∧ -x ≤ -2 (i.e., 2 ≤ x ≤ 5)
        A_s = np.array([
            [1],
            [-1],
        ], dtype=np.int64)
        b_s = np.array([5, -2], dtype=np.int64)

        # Middle premise (empty)
        C = np.zeros((0, 1), dtype=np.int64)
        d = np.zeros(0, dtype=np.int64)

        # Disjunctive conclusion (two cases)
        E_list = [
            np.array([[1]], dtype=np.int64),   # E_1: x > 1
            np.array([[-1]], dtype=np.int64),  # E_2: -x > -6 (i.e., x < 6)
        ]
        f_list = [
            np.array([1], dtype=np.int64),
            np.array([-6], dtype=np.int64),
        ]

        dual = build_farkas_dual_disjunctive(A_s, b_s, C, d, E_list, f_list)

        # Should have λ_s (2) and μ_p (2 from E_1, E_2)
        assert len(dual.lambda_s_indices) == 2
        assert len(dual.mu_p_indices) == 2
        assert dual.n_vars == 1

    def test_multi_row_disjuncts(self):
        """Test disjunctive where each E_k has multiple rows.

        Example for initial obligation: checking V(x,q) ≥ 0 ∧ guard satisfied.
        """
        # Premise: x = 0
        A_s = np.array([
            [1],   # x ≤ 0
            [-1],  # -x ≤ 0
        ], dtype=np.int64)
        b_s = np.array([0, 0], dtype=np.int64)

        # Middle premise (empty)
        C = np.zeros((0, 1), dtype=np.int64)
        d = np.zeros(0, dtype=np.int64)

        # Disjunctive conclusion with multi-row E_k
        # E_1 has 2 rows: [x > -1; -x > 0] checking V ≥ 0 and guard
        E_list = [
            np.array([
                [1],   # Row 1: x > -1 (ranking ≥ 0)
                [-1],  # Row 2: -x > 0 (guard: x ≤ 0, converted to strict)
            ], dtype=np.int64)
        ]
        f_list = [
            np.array([-1, 0], dtype=np.int64)
        ]

        dual = build_farkas_dual_disjunctive(A_s, b_s, C, d, E_list, f_list)

        # Should have λ_s (2) and μ_p (2 from E_1's 2 rows)
        assert len(dual.lambda_s_indices) == 2
        assert len(dual.mu_p_indices) == 2
        assert dual.n_vars == 1
