"""Unit tests for verification obligations."""

import pytest
from zkterm_tool.parser import parse_with_constants
from zkterm_tool.verifier import Verifier


def test_initial_verification_simple_pass():
    """
    Test initial condition verification - should pass.

    Program:
        init: x = 0
        rank(q0):
            [] x >= 0 -> 10 - x

    The ranking value at x=0 is 10, which is positive.
    """
    program = """
        init: x = 0

        rank(q0):
            [] x >= 0 -> 10 - x
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    # Should have exactly 1 obligation (initial for q0)
    assert len(verification.obligations) == 1

    obl = verification.obligations[0]
    assert obl.obligation_type == "initial"
    assert obl.ranking_state == "q0"
    assert obl.passed is True
    assert obl.witness is not None

    # Overall verification should pass
    assert verification.passed is True


def test_initial_verification_with_bounds_pass():
    """
    Test initial condition with bounded variable.

    Program:
        init: x = 5 && y = 0
        rank(q0):
            [] x >= 0 && y >= 0 -> x + y

    At (x, y) = (5, 0), ranking value is 5 > 0.
    """
    program = """
        init: x = 5 && y = 0

        rank(q0):
            [] x >= 0 && y >= 0 -> x + y
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    assert len(verification.obligations) == 1
    assert verification.obligations[0].passed is True
    assert verification.passed is True


def test_initial_verification_multiple_states():
    """
    Test with multiple automaton states and ranking functions.

    Program:
        init: x = 0
        rank(q0):
            [] x >= 0 -> 10 - x
        rank(q1):
            [] x >= 0 -> 5 - x
    """
    program = """
        init: x = 0

        rank(q0):
            [] x >= 0 -> 10 - x

        rank(q1):
            [] x >= 0 -> 5 - x
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    # Should have 2 obligations (one for each state)
    assert len(verification.obligations) == 2

    # Both should pass
    for obl in verification.obligations:
        assert obl.obligation_type == "initial"
        assert obl.passed is True

    assert verification.passed is True


def test_initial_verification_fail_not_positive():
    """
    Test initial verification failure when ranking value is not positive.

    Program:
        init: x = 10
        rank(q0):
            [] x >= 0 -> 10 - x

    At x=10, the ranking value is 0, which is not > 0.
    """
    program = """
        init: x = 10

        rank(q0):
            [] x >= 0 -> 10 - x
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    assert len(verification.obligations) == 1
    assert verification.obligations[0].passed is False
    assert verification.passed is False


def test_initial_verification_vacuous_truth():
    """
    Test initial verification with contradictory premises.

    Program:
        init: x = -1
        rank(q0):
            [] x >= 0 -> 10 - x

    At x=-1, the guard x >= 0 is not satisfied. However, from a logical
    perspective, the implication "x = -1 ∧ x >= 0 ⟹ ..." holds vacuously
    because the premise is contradictory (no such x exists).

    This passes verification because there are no initial states that violate
    the ranking constraint (since there are no initial states that satisfy
    the ranking guard at all).
    """
    program = """
        init: x = -1

        rank(q0):
            [] x >= 0 -> 10 - x
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    assert len(verification.obligations) == 1
    # Passes vacuously because premise is contradictory
    assert verification.obligations[0].passed is True
    assert verification.passed is True


def test_verifier_requires_ranking_function():
    """Test that verifier raises error if no ranking functions provided."""
    program = """
        init: x = 0
        [] x < 10 -> x = x + 1
    """

    result = parse_with_constants(program)

    with pytest.raises(ValueError, match="No ranking functions"):
        Verifier(result)


def test_verification_result_summary():
    """Test VerificationResult.summary() method."""
    program = """
        init: x = 0

        rank(q0):
            [] x >= 0 -> 10 - x

        rank(q1):
            [] x >= 0 -> 5 - x
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    summary = verification.summary()
    assert summary == "2/2 obligations verified"


def test_verification_get_witnesses():
    """Test VerificationResult.get_witnesses() method."""
    program = """
        init: x = 0

        rank(q0):
            [] x >= 0 -> 10 - x
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    witnesses = verification.get_witnesses()
    assert len(witnesses) == 1
    assert isinstance(witnesses[0], dict)
    # Should contain Farkas multipliers
    assert any("lambda" in k or "mu" in k for k in witnesses[0].keys())


def test_initial_with_constant():
    """Test initial verification with named constant."""
    program = """
        const maxVal = 10

        init: x = 0

        rank(q0):
            [] x >= 0 && x < maxVal -> maxVal - x
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    assert len(verification.obligations) == 1
    assert verification.obligations[0].passed is True
    assert verification.passed is True


def test_transition_verification_simple():
    """
    Test well-defined and non-increasing obligations for a simple counter.

    Program:
        init: x = 0
        [] x < 10 -> x = x + 1
        rank(q0):
            [] x >= 0 && x < 11 -> 11 - x
        trans(q0, q0): x < 10

    Note: Guard is x < 11 (not x < 10) to ensure the ranking value
    is still positive after the transition (when x' could be 10).
    """
    program = """
        init: x = 0

        [] x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 && x < 11 -> 11 - x

        trans(q0, q0): x < 10
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    # Should have: 1 initial + 1 well_defined + 1 non_increasing
    assert len(verification.obligations) == 3

    # Check obligation types
    types = [o.obligation_type for o in verification.obligations]
    assert "initial" in types
    assert "well_defined" in types
    assert "non_increasing" in types

    # All should pass
    assert all(o.passed for o in verification.obligations)
    assert verification.passed is True


def test_fair_transition_strictly_decreasing():
    """
    Test strictly decreasing obligation for fair transition.

    Program:
        init: x = 10
        [] x > 1 -> x = x - 1
        rank(q0):
            [] x > 0 -> x
        trans!(q0, q0): x > 1

    Note: Guard is x > 0 (not x >= 0) to ensure ranking value is strictly positive.
    Transition requires x > 1 to ensure x' > 0 after decrement.
    """
    program = """
        init: x = 10

        [] x > 1 -> x = x - 1

        rank(q0):
            [] x > 0 -> x

        trans!(q0, q0): x > 1
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    # Should have: 1 initial + 1 well_defined + 1 non_increasing + 1 strictly_decreasing
    assert len(verification.obligations) == 4

    types = [o.obligation_type for o in verification.obligations]
    assert "initial" in types
    assert "well_defined" in types
    assert "non_increasing" in types
    assert "strictly_decreasing" in types

    # All should pass
    assert all(o.passed for o in verification.obligations)
    assert verification.passed is True


def test_transition_fail_not_decreasing():
    """
    Test failure when ranking function increases.

    Program:
        init: x = 0
        [] x < 10 -> x = x + 1
        rank(q0):
            [] x >= 0 -> x  # Wrong: increases instead of decreases!
        trans(q0, q0): x < 10
    """
    program = """
        init: x = 0

        [] x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 -> x

        trans(q0, q0): x < 10
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    # Should fail the non_increasing obligation
    assert not verification.passed

    # Find the non_increasing obligation
    non_inc = [o for o in verification.obligations if o.obligation_type == "non_increasing"]
    assert len(non_inc) == 1
    assert non_inc[0].passed is False


def test_transition_two_states():
    """
    Test with two automaton states.

    Program:
        init: x = 0
        [] x < 5 -> x = x + 1
        [] x >= 5 && x < 10 -> x = x + 1
        rank(q0):
            [] x >= 0 -> 10 - x
        rank(q1):
            [] x >= 0 -> 5 - x
        trans(q0, q1): x >= 5
        trans(q1, q1): x < 10
    """
    program = """
        init: x = 0

        [] x < 5 -> x = x + 1
        [] x >= 5 && x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 -> 10 - x

        rank(q1):
            [] x >= 0 -> 5 - x

        trans(q0, q1): x >= 5
        trans(q1, q1): x < 10
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    # Check we have obligations for both states
    states = {o.ranking_state for o in verification.obligations if o.ranking_state}
    assert "q0" in states
    assert "q1" in states
