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
    # New system: 1 update only (no infinity cases, so no initial_non_infinity/transition_non_infinity)
    # 1 prog_trans × 1 aut_trans × 1 source_case × 1 target_case = 1
    assert len(verification.obligations) == 1


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
    # Should fail update obligation (ranking increases, not decreases)
    failed = verification.failed_obligations()
    assert len(failed) == 1
    assert failed[0].obligation_type == "update"


def test_fair_transition_pass():
    """Test program with fair transition that should pass."""
    program = """
        init: x = 10

        [] x > 1 -> x = x - 1

        rank(q0):
            [] x > 0 -> x
            [] x <= 0 -> inf

        trans!(q0, q0): x > 1
    """

    result = parse_with_constants(program)
    verification = verify_termination(result)

    assert verification.passed is True
    # New system with infinity case: 1 initial_non_infinity + 1 transition_non_infinity + 1 update = 3
    # Fair transitions use ζ=1 in the update obligation
    assert len(verification.obligations) == 3

    # Check that the update obligation is marked as fair
    update_obls = [o for o in verification.obligations if o.obligation_type == "update"]
    assert len(update_obls) == 1
    assert update_obls[0].is_fair is True


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
    # New: source_ranking_state instead of ranking_state
    states = {o.source_ranking_state for o in verification.obligations if o.source_ranking_state}
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
    # New system: 2 update (one per prog trans, no infinity cases)
    # 2 prog_trans × 1 aut_trans × 1 source_case × 1 target_case = 2
    assert len(verification.obligations) == 2


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

    # New: Should raise ValueError when no automaton transitions
    # Old: Would just have initial obligation
    with pytest.raises(ValueError, match="No automaton transitions provided"):
        verify_termination(result)


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


def test_partial_guard_verification_pass():
    """Test verification with guard mentioning only subset of variables."""
    program = """
        init: x = 0 && y = 0

        [] x < 10 && y < 10 -> x = x + 1; y = y + 1

        rank(q0):
            [] x >= 0 && x <= 10 -> 11 - x

        trans(q0, q0): x < 10 && y < 10
    """

    result = parse_with_constants(program)
    verification = verify_termination(result)

    # Should pass - y is implicitly unconstrained in ranking guard
    # The ranking function only constrains x, so as long as x decreases, termination holds
    assert verification.passed is True


def test_true_keyword_in_ranking_function():
    """Test verification with 'true' keyword in ranking function."""
    program = """
        init: x = 0

        [] x < 10 -> x = x + 1

        rank(q0):
            [] true -> 10 - x

        trans(q0, q0): x < 10
    """

    result = parse_with_constants(program)
    verification = verify_termination(result)

    # This should pass because:
    # - Ranking function is always defined (guard is 'true')
    # - Initial obligation: x=0 => 10-x = 10 > 0 ✓
    # - Non-increasing: x < 10 ∧ x' = x+1 => (10-x) >= (10-x') = (10-x-1)
    #   => 10-x >= 9-x => 1 >= 0 ✓
    assert verification.passed is True


def test_true_keyword_in_init_condition():
    """Test verification with 'true' init condition (always initialized)."""
    program = """
        init: true

        [] x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 && x < 11 -> 11 - x

        trans(q0, q0): x < 10
    """

    result = parse_with_constants(program)
    verification = verify_termination(result)

    # With 'init: true', there are no constraints on the initial state.
    # New system: No initial_non_infinity obligations (no infinity cases)
    # The ranking function covers all states where it matters (x in [0, 10])
    # So verification should pass
    assert verification.passed is True

    # Should have update obligations (no initial_non_infinity since no infinity cases)
    update_obls = [o for o in verification.obligations if o.obligation_type == "update"]
    assert len(update_obls) >= 1
    assert all(o.passed for o in update_obls)


def test_multiple_variables_partial_ranking_guards():
    """Test program with multiple variables where ranking guards mention only some."""
    program = """
        init: x = 0 && y = 0 && z = 0

        [] x < 5 && y < 5 && z < 5 -> x = x + 1; y = y + 1; z = z + 1

        rank(q0):
            [] x >= 0 && x <= 5 -> 6 - x

        trans(q0, q0): x < 5 && y < 5 && z < 5
    """

    result = parse_with_constants(program)
    verification = verify_termination(result)

    # The ranking function only mentions x, but y and z are also program variables
    # This should pass because x is always decreasing, regardless of y and z
    assert verification.passed is True


def test_ranking_function_with_unconstrained_variables():
    """
    Test that ranking functions work correctly when they don't mention all program variables.

    This is a regression test for a bug where ranking functions that only mentioned
    a subset of program variables would cause a shape mismatch error during verification.
    """
    program = """
        const maxVal = 10

        init: x = 0 && y = 0 && z = 0

        [] x < maxVal && y < maxVal && z < maxVal -> x = x + 1; y = y + 1; z = z + 1

        rank(q0):
            [] x >= 0 && x <= maxVal -> maxVal + 1 - x

        trans(q0, q0): x < maxVal && y < maxVal && z < maxVal
    """

    result = parse_with_constants(program)
    verification = verify_termination(result)

    # Should verify successfully - ranking function only depends on x,
    # while y and z are unconstrained in the ranking but still part of the program
    assert verification.passed is True
    # New system: 1 update only (no infinity cases)
    assert len(verification.obligations) == 1
