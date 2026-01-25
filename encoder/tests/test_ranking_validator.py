"""Tests for ranking function validation."""

import pytest
from zkterm_tool.parser import parse_with_constants
from zkterm_tool.ranking_encoder import encode_ranking_functions
from zkterm_tool.ranking_validator import (
    check_disjoint_cases,
    check_complete_coverage,
    check_non_negativity,
    validate_ranking_function,
)


def test_non_negativity_pass():
    """Test that non-negative ranking functions pass validation."""
    program = """
        rank(q0):
            [] x >= 0 && x <= 10 -> 10 - x
            [] x < 0 -> inf
            [] x > 10 -> inf
    """

    result = parse_with_constants(program)
    encodings = encode_ranking_functions(result.ranking_functions)
    enc = encodings["q0"]

    is_valid, error_msg = check_non_negativity(enc.finite_cases, enc.variables)
    assert is_valid
    assert error_msg == ""


def test_non_negativity_fail():
    """Test that ranking functions with negative values fail validation."""
    program = """
        rank(q0):
            [] x >= 0 -> 10 - x
    """

    result = parse_with_constants(program)
    encodings = encode_ranking_functions(result.ranking_functions)
    enc = encodings["q0"]

    # This ranking can be negative (e.g., at x=11, ranking = 10-11 = -1)
    is_valid, error_msg = check_non_negativity(enc.finite_cases, enc.variables)
    assert not is_valid
    assert "negative ranking value" in error_msg
    assert "case 0" in error_msg


def test_non_negativity_multiple_cases():
    """Test non-negativity with multiple cases."""
    program = """
        rank(q0):
            [] x >= 0 && x < 5 -> 5 - x
            [] x >= 5 && x <= 10 -> 10 - x
    """

    result = parse_with_constants(program)
    encodings = encode_ranking_functions(result.ranking_functions)
    enc = encodings["q0"]

    # Both cases are non-negative within their guards
    is_valid, error_msg = check_non_negativity(enc.finite_cases, enc.variables)
    assert is_valid
    assert error_msg == ""


def test_disjoint_cases_pass():
    """Test that disjoint cases pass validation."""
    program = """
        rank(q0):
            [] x >= 0 && x < 5 -> 10 - x
            [] x >= 5 && x < 10 -> 20 - x
            [] x < 0 -> inf
            [] x >= 10 -> inf
    """

    result = parse_with_constants(program)
    encodings = encode_ranking_functions(result.ranking_functions)
    enc = encodings["q0"]

    is_valid, error_msg = check_disjoint_cases(enc.finite_cases, enc.infinity_cases, enc.variables)
    assert is_valid
    assert error_msg == ""


def test_disjoint_cases_fail():
    """Test that overlapping cases fail validation."""
    program = """
        rank(q0):
            [] x >= 0 && x < 10 -> 10 - x
            [] x >= 5 && x < 15 -> 15 - x
    """

    result = parse_with_constants(program)
    encodings = encode_ranking_functions(result.ranking_functions)
    enc = encodings["q0"]

    is_valid, error_msg = check_disjoint_cases(enc.finite_cases, enc.infinity_cases, enc.variables)
    assert not is_valid
    assert "not disjoint" in error_msg
    assert "overlap" in error_msg


def test_complete_coverage_pass():
    """Test that complete coverage passes validation."""
    program = """
        rank(q0):
            [] x >= 0 -> 10 - x
            [] x < 0 -> inf
    """

    result = parse_with_constants(program)
    encodings = encode_ranking_functions(result.ranking_functions)
    enc = encodings["q0"]

    is_valid, error_msg = check_complete_coverage(enc.finite_cases, enc.infinity_cases, enc.variables)
    assert is_valid
    assert error_msg == ""


def test_complete_coverage_fail():
    """Test that incomplete coverage fails validation."""
    program = """
        rank(q0):
            [] x >= 0 && x < 10 -> 10 - x
    """

    result = parse_with_constants(program)
    encodings = encode_ranking_functions(result.ranking_functions)
    enc = encodings["q0"]

    # Missing cases for x < 0 and x >= 10
    is_valid, error_msg = check_complete_coverage(enc.finite_cases, enc.infinity_cases, enc.variables)
    assert not is_valid
    assert "do not cover entire state space" in error_msg


def test_complete_coverage_with_true_guard():
    """Test that 'true' guard provides complete coverage."""
    program = """
        rank(q0):
            [] true -> 10 - x
    """

    result = parse_with_constants(program)
    encodings = encode_ranking_functions(result.ranking_functions)
    enc = encodings["q0"]

    is_valid, error_msg = check_complete_coverage(enc.finite_cases, enc.infinity_cases, enc.variables)
    assert is_valid
    assert error_msg == ""


def test_validate_ranking_function_all_pass():
    """Test full validation with all checks passing."""
    program = """
        rank(q0):
            [] x >= 0 && x <= 10 -> 10 - x
            [] x < 0 -> inf
            [] x > 10 -> inf
    """

    result = parse_with_constants(program)
    encodings = encode_ranking_functions(result.ranking_functions)
    enc = encodings["q0"]

    is_valid, errors = validate_ranking_function(enc.finite_cases, enc.infinity_cases, enc.variables)
    assert is_valid
    assert len(errors) == 0


def test_validate_ranking_function_multiple_failures():
    """Test full validation with multiple failures."""
    program = """
        rank(q0):
            [] x >= 0 && x < 10 -> 10 - x
            [] x >= 5 && x < 15 -> 15 - x
    """

    result = parse_with_constants(program)
    encodings = encode_ranking_functions(result.ranking_functions)
    enc = encodings["q0"]

    is_valid, errors = validate_ranking_function(enc.finite_cases, enc.infinity_cases, enc.variables)
    assert not is_valid
    assert len(errors) >= 2  # Should have disjointness and coverage failures


def test_non_negativity_two_variables():
    """Test non-negativity check with two variables."""
    program = """
        rank(q0):
            [] x >= 0 && y >= 0 && x + y <= 10 -> x + y
            [] x < 0 -> inf
            [] y < 0 -> inf
            [] x + y > 10 -> inf
    """

    result = parse_with_constants(program)
    encodings = encode_ranking_functions(result.ranking_functions)
    enc = encodings["q0"]

    # Ranking is always >= 0 within the guard
    is_valid, error_msg = check_non_negativity(enc.finite_cases, enc.variables)
    assert is_valid


def test_non_negativity_fail_two_variables():
    """Test non-negativity failure with two variables."""
    program = """
        rank(q0):
            [] x >= 0 && y >= 0 -> x + y - 20
    """

    result = parse_with_constants(program)
    encodings = encode_ranking_functions(result.ranking_functions)
    enc = encodings["q0"]

    # At x=0, y=0: ranking = 0 + 0 - 20 = -20 (negative!)
    is_valid, error_msg = check_non_negativity(enc.finite_cases, enc.variables)
    assert not is_valid
    assert "negative ranking value" in error_msg
