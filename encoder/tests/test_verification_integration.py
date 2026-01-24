"""Integration tests for end-to-end verification."""

import pytest
from zkterm_tool import parse_with_constants, verify_termination


def test_simple_counter_pass():
    """Test simple counter program that should pass verification."""
    program = """
        const maxVal = 10

        init: x = 0

        [] x < maxVal -> x = x + 1

        rank(q0):
            [] x >= 0 && x < maxVal + 1 -> maxVal + 1 - x

        trans(q0, q0): x < maxVal
    """

    result = parse_with_constants(program)
    verification = verify_termination(result)

    assert verification.passed is True
    assert len(verification.obligations) == 3  # initial, well_defined, non_increasing


def test_simple_counter_fail():
    """Test counter with wrong ranking function that should fail."""
    program = """
        init: x = 0

        [] x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 -> x

        trans(q0, q0): x < 10
    """

    result = parse_with_constants(program)
    verification = verify_termination(result)

    assert verification.passed is False
    # Should fail non_increasing obligation
    failed = verification.failed_obligations()
    assert len(failed) == 1
    assert failed[0].obligation_type == "non_increasing"


def test_fair_transition_pass():
    """Test program with fair transition that should pass."""
    program = """
        init: x = 10

        [] x > 1 -> x = x - 1

        rank(q0):
            [] x > 0 -> x

        trans!(q0, q0): x > 1
    """

    result = parse_with_constants(program)
    verification = verify_termination(result)

    assert verification.passed is True
    # Should have initial, well_defined, non_increasing, strictly_decreasing
    assert len(verification.obligations) == 4

    types = [o.obligation_type for o in verification.obligations]
    assert "strictly_decreasing" in types


def test_two_state_automaton():
    """Test program with two-state Büchi automaton."""
    program = """
        const threshold = 5

        init: x = 0

        [] x < threshold -> x = x + 1
        [] x >= threshold && x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 -> 11 - x

        rank(q1):
            [] x >= 0 -> 6 - x

        trans(q0, q1): x >= threshold
        trans(q1, q1): x < 10
    """

    result = parse_with_constants(program)
    verification = verify_termination(result)

    # Check we have obligations for both states
    states = {o.ranking_state for o in verification.obligations if o.ranking_state}
    assert "q0" in states
    assert "q1" in states


def test_witnesses_are_integers():
    """Test that all Farkas witnesses are integers."""
    program = """
        init: x = 0

        [] x < 5 -> x = x + 1

        rank(q0):
            [] x >= 0 && x <= 5 -> 6 - x

        trans(q0, q0): x < 5
    """

    result = parse_with_constants(program)
    verification = verify_termination(result)

    witnesses = verification.get_witnesses()
    assert len(witnesses) > 0

    for witness in witnesses:
        for key, value in witness.items():
            assert isinstance(value, int), f"{key} = {value} is not an integer"


def test_multiple_transitions_same_automaton_state():
    """Test with multiple program transitions and same automaton state."""
    program = """
        init: x = 0

        [] x < 5 -> x = x + 1
        [] x >= 5 && x < 10 -> x = x + 2

        rank(q0):
            [] x >= 0 && x <= 11 -> 12 - x

        trans(q0, q0): x < 10
    """

    result = parse_with_constants(program)
    verification = verify_termination(result)

    # Should have obligations for both program transitions
    # 1 initial + 2 * (well_defined + non_increasing) = 5
    assert len(verification.obligations) == 5


def test_verification_summary_format():
    """Test that summary string is formatted correctly."""
    program = """
        init: x = 0

        [] x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 && x < 11 -> 11 - x

        trans(q0, q0): x < 10
    """

    result = parse_with_constants(program)
    verification = verify_termination(result)

    summary = verification.summary()
    # Should be like "3/3 obligations verified"
    assert "obligations verified" in summary
    assert "/" in summary


def test_no_automaton_transitions():
    """Test that verification requires automaton transitions."""
    program = """
        init: x = 0

        [] x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 -> 10 - x
    """

    result = parse_with_constants(program)
    verifier_result = verify_termination(result)

    # Without automaton transitions, only initial obligation
    assert len(verifier_result.obligations) == 1
    assert verifier_result.obligations[0].obligation_type == "initial"


def test_complex_guard_expressions():
    """Test with complex guard expressions."""
    program = """
        init: x = 0 && y = 0

        [] x < 5 && y < 5 -> x = x + 1; y = y + 1

        rank(q0):
            [] x >= 0 && y >= 0 && x <= 5 && y <= 5 -> 6 - x + 6 - y

        trans(q0, q0): x < 5 && y < 5
    """

    result = parse_with_constants(program)
    verification = verify_termination(result)

    # Should handle multiple variables
    assert verification.passed is True


def test_verification_with_constants():
    """Test verification with named constants throughout."""
    program = """
        const init_val = 0
        const max_val = 10
        const rank_bound = 11

        init: x = init_val

        [] x < max_val -> x = x + 1

        rank(q0):
            [] x >= 0 && x < rank_bound -> rank_bound - x

        trans(q0, q0): x < max_val
    """

    result = parse_with_constants(program)
    verification = verify_termination(result)

    assert verification.passed is True
