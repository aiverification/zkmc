"""Unit tests for verification obligations."""

import pytest
import warnings
from zkterm_tool.parser import parse_with_constants
from zkterm_tool.verifier import Verifier


def test_initial_verification_simple_pass():
    """
    Test initial condition verification - should pass.

    Program:
        init: x = 0
        rank(q0):
            [] x >= 0 -> 10 - x
        trans(q0, q0): true

    The ranking value at x=0 is 10, which is positive.
    """
    program = """
        init: x = 0

        rank(q0):
            [] x >= 0 -> 10 - x

        trans(q0, q0): true
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    # Should have 1 initial + 0 update (no program transitions) = 1
    assert len(verification.obligations) >= 1

    # Check initial obligation
    initial_obls = [o for o in verification.obligations if o.obligation_type == "initial"]
    assert len(initial_obls) == 1

    obl = initial_obls[0]
    assert obl.source_ranking_state == "q0"  # Updated field name
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

        trans(q0, q0): true
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

        trans(q1, q1): true
    """
    program = """
        init: x = 0

        rank(q0):
            [] x >= 0 -> 10 - x

        rank(q1):
            [] x >= 0 -> 5 - x

        trans(q1, q1): true
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


@pytest.mark.xfail(reason="Known bug: verification incorrectly passes when ranking value is negative")
def test_initial_verification_fail_not_positive():
    """
    Test initial verification failure when ranking value is negative.

    Program:
        init: x = 11
        rank(q0):
            [] x >= 0 -> 10 - x

    At x=11, the ranking value is 10 - 11 = -1, which is < 0.
    This should fail because ranking values must be >= 0.
    """
    program = """
        init: x = 11

        rank(q0):
            [] x >= 0 -> 10 - x

        trans(q0, q0): true
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

        trans(q0, q0): true
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

        trans(q1, q1): true
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

        trans(q0, q0): true
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

        trans(q0, q0): true
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

    # Should have: 1 initial + 1 update
    assert len(verification.obligations) == 2

    # Check obligation types
    types = [o.obligation_type for o in verification.obligations]
    assert "initial" in types
    assert "update" in types

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

    # Should have: 1 initial + 1 update (with is_fair=True)
    assert len(verification.obligations) == 2

    types = [o.obligation_type for o in verification.obligations]
    assert "initial" in types
    assert "update" in types

    # Check that the update obligation is marked as fair
    update_obls = [o for o in verification.obligations if o.obligation_type == "update"]
    assert len(update_obls) == 1
    assert update_obls[0].is_fair is True

    # All should pass
    assert all(o.passed for o in verification.obligations)
    assert verification.passed is True


@pytest.mark.xfail(reason="Known bug: verification incorrectly passes when ranking increases")
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

    # Should fail the update obligation
    assert not verification.passed

    # Find the update obligation
    update_obls = [o for o in verification.obligations if o.obligation_type == "update"]
    assert len(update_obls) == 1
    assert update_obls[0].passed is False


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
    states = {o.source_ranking_state for o in verification.obligations if o.source_ranking_state}
    assert "q0" in states
    assert "q1" in states


def test_multiple_cases_support():
    """
    Test that multi-case ranking functions are now supported.

    The verifier should handle multiple cases via disjunctive obligations.
    """
    program = """
        init: x = 0

        [] x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 && x < 5 -> 10 - x
            [] x >= 5 && x < 10 -> 20 - x

        trans(q0, q0): x < 10
    """

    result = parse_with_constants(program)

    # Should not issue any warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        verifier = Verifier(result)
        verification = verifier.verify_all()

        # Check that no warnings were issued
        assert len(w) == 0

    # Should have: 1 initial + 2 update (one per source case)
    assert len(verification.obligations) == 3

    # Check obligation types
    update_obls = [o for o in verification.obligations if o.obligation_type == "update"]
    assert len(update_obls) == 2

    # Both update obligations should have different source case indices
    case_indices = {o.source_case_idx for o in update_obls}
    assert case_indices == {0, 1}


def test_single_case_no_warning():
    """
    Test that no warning is issued when ranking functions have a single case.
    """
    program = """
        init: x = 0

        [] x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 && x <= 10 -> 11 - x

        trans(q0, q0): x < 10
    """

    result = parse_with_constants(program)

    # Should not issue any warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        verifier = Verifier(result)
        verification = verifier.verify_all()

        # Check that no warnings were issued
        assert len(w) == 0

    # Should have: 1 initial + 1 update
    assert len(verification.obligations) == 2

    # Check that single case works correctly
    update_obls = [o for o in verification.obligations if o.obligation_type == "update"]
    assert len(update_obls) == 1
    assert update_obls[0].source_case_idx == 0


def test_multiple_cases_multiple_states_support():
    """
    Test multi-case support for multiple states.

    Should handle multiple cases correctly for each state.
    """
    program = """
        init: x = 0

        [] x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 && x < 5 -> 10 - x
            [] x >= 5 && x < 11 -> 20 - x

        rank(q1):
            [] x >= 0 && x < 11 -> 11 - x

        rank(q2):
            [] x >= 0 && x < 3 -> 8 - x
            [] x >= 3 && x < 11 -> 12 - x

        trans(q0, q1): x < 5
        trans(q1, q2): x >= 5
        trans(q2, q2): x < 10
    """

    result = parse_with_constants(program)

    # Should not issue any warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        verifier = Verifier(result)
        verification = verifier.verify_all()

        # Check that no warnings were issued
        assert len(w) == 0

    # Should have: 3 initial (one per state) + update obligations
    initial_obls = [o for o in verification.obligations if o.obligation_type == "initial"]
    assert len(initial_obls) == 3

    # Update obligations: q0->q1 (2 source cases), q1->q2 (1 source case), q2->q2 (2 source cases)
    update_obls = [o for o in verification.obligations if o.obligation_type == "update"]
    assert len(update_obls) == 5  # 2 + 1 + 2

    # Check states are present
    states = {o.source_ranking_state for o in initial_obls}
    assert states == {"q0", "q1", "q2"}
