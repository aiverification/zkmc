"""Tests for ranking function evaluation on concrete states."""

import pytest
import numpy as np
from zkterm_tool import parse_with_constants, encode_ranking_functions
from zkterm_tool.ranking_evaluator import (
    check_guard, evaluate_ranking, check_automaton_guard
)
from zkterm_tool.automaton_encoder import encode_automaton_transitions


def test_check_guard_satisfied():
    """Test guard checking with satisfied constraints."""
    state_vec = np.array([5, 3])
    # Guard: x <= 10 && y <= 5
    C_j = np.array([[1, 0], [0, 1]])
    d_j = np.array([10, 5])

    assert check_guard(state_vec, C_j, d_j) is True


def test_check_guard_not_satisfied():
    """Test guard checking with unsatisfied constraints."""
    state_vec = np.array([15, 3])
    # Guard: x <= 10 && y <= 5
    C_j = np.array([[1, 0], [0, 1]])
    d_j = np.array([10, 5])

    assert check_guard(state_vec, C_j, d_j) is False


def test_check_guard_empty():
    """Test that empty guard is always true."""
    state_vec = np.array([100, 200])
    C_j = np.array([]).reshape(0, 2)  # Empty guard
    d_j = np.array([])

    assert check_guard(state_vec, C_j, d_j) is True


def test_evaluate_ranking_simple():
    """Test evaluating simple ranking function."""
    program = """
    rank(q0):
        [] x >= 0 && x <= 10 -> 10 - x
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)

    # State x=5 should give V = 10 - 5 = 5
    state = {"x": 5}
    value = evaluate_ranking(state, rank_encs["q0"])

    assert value == 5


def test_evaluate_ranking_undefined():
    """Test that V(s,q) = ∞ when no guard satisfied."""
    program = """
    rank(q0):
        [] x >= 0 && x < 5 -> 10 - x
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)

    # State x=10 doesn't satisfy guard, should return None (∞)
    state = {"x": 10}
    value = evaluate_ranking(state, rank_encs["q0"])

    assert value is None


def test_evaluate_ranking_multivar():
    """Test evaluating ranking function with multiple variables."""
    program = """
    rank(q0):
        [] x >= 0 && y >= 0 -> x + y
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)

    state = {"x": 5, "y": 3}
    value = evaluate_ranking(state, rank_encs["q0"])

    assert value == 8


def test_evaluate_ranking_first_match():
    """Test first-match semantics with multiple cases."""
    program = """
    rank(q0):
        [] x >= 5 -> 100
        [] x >= 0 -> 10 - x
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)

    # State x=7 satisfies both guards, should use first (100)
    state = {"x": 7}
    value = evaluate_ranking(state, rank_encs["q0"])

    assert value == 100


def test_evaluate_ranking_constant():
    """Test ranking function with constant value."""
    program = """
    rank(q0):
        [] x >= 0 -> 42
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)

    state = {"x": 5}
    value = evaluate_ranking(state, rank_encs["q0"])

    assert value == 42


def test_evaluate_ranking_with_true_guard():
    """Test ranking function with 'true' guard (unconditional)."""
    program = """
    rank(q0):
        [] true -> x + 10
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)

    # Should work for any x value
    state = {"x": 5}
    value = evaluate_ranking(state, rank_encs["q0"])
    assert value == 15

    state = {"x": -10}
    value = evaluate_ranking(state, rank_encs["q0"])
    assert value == 0


def test_check_automaton_guard_enabled():
    """Test checking enabled automaton transition."""
    program = """
    trans(q0, q1): x >= 0 && x < 10
    """
    result = parse_with_constants(program)
    aut_encs = encode_automaton_transitions(result.automaton_transitions)

    # State x=5 should enable the transition
    state = {"x": 5}
    enabled = check_automaton_guard(state, aut_encs[0])

    assert enabled is True


def test_check_automaton_guard_disabled():
    """Test checking disabled automaton transition."""
    program = """
    trans(q0, q1): x >= 0 && x < 10
    """
    result = parse_with_constants(program)
    aut_encs = encode_automaton_transitions(result.automaton_transitions)

    # State x=15 should not enable the transition
    state = {"x": 15}
    enabled = check_automaton_guard(state, aut_encs[0])

    assert enabled is False


def test_check_automaton_guard_multivar():
    """Test automaton guard with multiple variables."""
    program = """
    trans(q0, q1): x >= 0 && y < 5
    """
    result = parse_with_constants(program)
    aut_encs = encode_automaton_transitions(result.automaton_transitions)

    # Both constraints satisfied
    state = {"x": 3, "y": 2}
    assert check_automaton_guard(state, aut_encs[0]) is True

    # First satisfied, second not
    state = {"x": 3, "y": 10}
    assert check_automaton_guard(state, aut_encs[0]) is False


def test_evaluate_ranking_boundary():
    """Test ranking evaluation at boundary values."""
    program = """
    rank(q0):
        [] x >= 0 && x <= 10 -> 10 - x
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)

    # Minimum boundary
    state = {"x": 0}
    assert evaluate_ranking(state, rank_encs["q0"]) == 10

    # Maximum boundary
    state = {"x": 10}
    assert evaluate_ranking(state, rank_encs["q0"]) == 0

    # Just outside boundaries
    state = {"x": -1}
    assert evaluate_ranking(state, rank_encs["q0"]) is None

    state = {"x": 11}
    assert evaluate_ranking(state, rank_encs["q0"]) is None
