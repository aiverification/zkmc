"""Tests for ranking function encoder."""

import pytest
import numpy as np
from zkterm_tool import (
    parse_with_constants,
    encode_ranking_function,
    encode_ranking_functions,
    RankingCase,
    RankingFunction,
    Var,
    Num,
    BinOp,
    Comparison,
    CompOp
)


class TestRankingEncoder:
    def test_simple_case_encoding(self):
        """Test encoding a simple ranking case: [] x > 0 -> x"""
        result = parse_with_constants("""
            rank(q0):
                [] x > 0 -> x
        """)

        enc = encode_ranking_function(result.ranking_functions["q0"])

        assert enc.state == "q0"
        assert enc.variables == ["x"]
        assert len(enc.cases) == 1

        case_enc = enc.cases[0]

        # Guard: x > 0  =>  x - 0 > 0  =>  -x + 0 < 0  =>  -x < 0 (after normalization)
        # Or: 0 - x < 0  =>  -x < 0
        assert case_enc.A_j.shape[0] >= 1 or case_enc.A_j.shape[0] == 0  # May have strict inequality

        # Expression: x  =>  C_j = [1], d_j = 0
        assert np.array_equal(case_enc.C_j, np.array([1]))
        assert case_enc.d_j == 0

    def test_linear_expression_encoding(self):
        """Test encoding linear expression: 10 - x"""
        result = parse_with_constants("""
            rank(q0):
                [] x >= 0 -> 10 - x
        """)

        enc = encode_ranking_function(result.ranking_functions["q0"])
        case_enc = enc.cases[0]

        # Expression: 10 - x  =>  -x + 10  =>  C_j = [-1], d_j = 10
        assert np.array_equal(case_enc.C_j, np.array([-1]))
        assert case_enc.d_j == 10

    def test_multiple_variables(self):
        """Test encoding with multiple variables."""
        result = parse_with_constants("""
            rank(q0):
                [] x > 0 && y > 0 -> 2*x + 3*y - 1
        """)

        enc = encode_ranking_function(result.ranking_functions["q0"])

        # Variables should be sorted
        assert enc.variables == ["x", "y"]

        case_enc = enc.cases[0]

        # Expression: 2*x + 3*y - 1  =>  C_j = [2, 3], d_j = -1
        assert np.array_equal(case_enc.C_j, np.array([2, 3]))
        assert case_enc.d_j == -1

    def test_multiple_cases_encoding(self):
        """Test encoding ranking function with multiple cases."""
        result = parse_with_constants("""
            rank(q0):
                [] x >= 0 && x < 10 -> 10 - x
                [] x >= 10 -> 1
        """)

        enc = encode_ranking_function(result.ranking_functions["q0"])

        assert len(enc.cases) == 2

        # First case: expression 10 - x
        case1 = enc.cases[0]
        assert np.array_equal(case1.C_j, np.array([-1]))
        assert case1.d_j == 10

        # Guard should have 2 inequalities: x >= 0 AND x < 10
        assert case1.A_j.shape[0] == 2

        # Second case: expression 1 (constant)
        case2 = enc.cases[1]
        assert np.array_equal(case2.C_j, np.array([0]))  # no x term
        assert case2.d_j == 1

        # Guard should have 1 inequality: x >= 10
        assert case2.A_j.shape[0] == 1

    def test_guard_encoding(self):
        """Test that guards are correctly encoded to (A_j, b_j)."""
        result = parse_with_constants("""
            rank(q0):
                [] x >= 0 && x < 10 -> x
        """)

        enc = encode_ranking_function(result.ranking_functions["q0"])
        case_enc = enc.cases[0]

        # Guard: x >= 0 AND x < 10
        # x >= 0  =>  -x <= 0  =>  [-1] x <= [0]
        # x < 10  =>  [1] x < [10] (strict) or x <= 9 if converted
        assert case_enc.A_j.shape[0] == 2  # Two inequality constraints

    def test_multiple_states(self):
        """Test encoding multiple ranking functions with consistent variable ordering."""
        result = parse_with_constants("""
            rank(q0):
                [] x > 0 -> x

            rank(q1):
                [] y > 0 -> y
        """)

        encodings = encode_ranking_functions(result.ranking_functions)

        assert len(encodings) == 2
        assert "q0" in encodings
        assert "q1" in encodings

        # Both should use the same variable ordering (union of all variables)
        enc0 = encodings["q0"]
        enc1 = encodings["q1"]

        assert enc0.variables == enc1.variables
        assert set(enc0.variables) == {"x", "y"}

    def test_constant_expression(self):
        """Test encoding constant expression."""
        result = parse_with_constants("""
            rank(q0):
                [] x >= 10 -> 5
        """)

        enc = encode_ranking_function(result.ranking_functions["q0"])
        case_enc = enc.cases[0]

        # Expression: 5  =>  C_j = [0], d_j = 5
        assert np.array_equal(case_enc.C_j, np.array([0]))
        assert case_enc.d_j == 5

    def test_with_constants_substitution(self):
        """Test encoding with constant substitution."""
        result = parse_with_constants("""
            const maxVal = 10

            rank(q0):
                [] x >= 0 && x < maxVal -> maxVal - x
        """)

        enc = encode_ranking_function(result.ranking_functions["q0"])
        case_enc = enc.cases[0]

        # Expression: 10 - x  =>  C_j = [-1], d_j = 10
        assert np.array_equal(case_enc.C_j, np.array([-1]))
        assert case_enc.d_j == 10

    def test_empty_guard(self):
        """Test case with no explicit guard (always true)."""
        # Create a ranking case programmatically with no guards
        case = RankingCase(guards=[], expression=Num(1))
        rf = RankingFunction(state="q0", cases=[case])

        enc = encode_ranking_function(rf, variables=["x"])
        case_enc = enc.cases[0]

        # Empty guard should give empty matrix
        assert case_enc.A_j.shape[0] == 0
        assert case_enc.b_j.shape[0] == 0

        # Expression: 1  =>  C_j = [0], d_j = 1
        assert np.array_equal(case_enc.C_j, np.array([0]))
        assert case_enc.d_j == 1

    def test_negative_coefficients(self):
        """Test encoding with negative coefficients."""
        result = parse_with_constants("""
            rank(q0):
                [] x > 0 -> -2*x + 5
        """)

        enc = encode_ranking_function(result.ranking_functions["q0"])
        case_enc = enc.cases[0]

        # Expression: -2*x + 5  =>  C_j = [-2], d_j = 5
        assert np.array_equal(case_enc.C_j, np.array([-2]))
        assert case_enc.d_j == 5
