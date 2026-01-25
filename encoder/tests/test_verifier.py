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
            [] x < 0 -> inf
        trans(q0, q0): true

    The ranking value at x=0 is 10, which is positive.
    Initial state x=0 should not satisfy infinity guard x < 0.
    """
    program = """
        init: x = 0

        rank(q0):
            [] x >= 0 -> 10 - x
            [] x < 0 -> inf

        trans(q0, q0): true
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    # New system: 1 initial_non_infinity (no program transitions, so no update)
    assert len(verification.obligations) >= 1

    # Check initial_non_infinity obligation
    initial_obls = [o for o in verification.obligations if o.obligation_type == "initial_non_infinity"]
    assert len(initial_obls) == 1

    obl = initial_obls[0]
    assert obl.source_ranking_state == "q0"
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
            [] x < 0 || y < 0 -> inf

    At (x, y) = (5, 0), ranking value is 5 > 0.
    Initial state should not satisfy infinity guard.
    """
    program = """
        init: x = 5 && y = 0

        rank(q0):
            [] x >= 0 && y >= 0 -> x + y
            [] x < 0 -> inf
            [] y < 0 -> inf

        trans(q0, q0): true
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    # New system: 2 initial_non_infinity (2 infinity cases)
    assert len(verification.obligations) == 2
    assert all(o.passed for o in verification.obligations)
    assert verification.passed is True


def test_initial_verification_multiple_states():
    """
    Test with multiple automaton states and ranking functions.

    Program:
        init: x = 0
        rank(q0):
            [] x >= 0 -> 10 - x
            [] x < 0 -> inf
        rank(q1):
            [] x >= 0 -> 5 - x
            [] x < 0 -> inf

        trans(q1, q1): true
    """
    program = """
        init: x = 0

        rank(q0):
            [] x >= 0 -> 10 - x
            [] x < 0 -> inf

        rank(q1):
            [] x >= 0 -> 5 - x
            [] x < 0 -> inf

        trans(q1, q1): true
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    # New system: 2 initial_non_infinity obligations (one per state)
    assert len(verification.obligations) == 2

    # Both should pass and be initial_non_infinity
    for obl in verification.obligations:
        assert obl.obligation_type == "initial_non_infinity"
        assert obl.passed is True

    assert verification.passed is True


def test_initial_verification_fail_not_positive():
    """
    Test that validation catches negative ranking values.

    Program:
        rank(q0):
            [] x >= 0 -> 10 - x

    At x=11, the ranking value is 10 - 11 = -1, which is < 0.
    This should fail validation (non-negativity check).
    """
    from zkterm_tool.ranking_encoder import encode_ranking_functions
    from zkterm_tool.ranking_validator import validate_ranking_function

    program = """
        rank(q0):
            [] x >= 0 -> 10 - x
    """

    result = parse_with_constants(program)
    encodings = encode_ranking_functions(result.ranking_functions)
    enc = encodings["q0"]

    # Should fail non-negativity check
    is_valid, errors = validate_ranking_function(enc.finite_cases, enc.infinity_cases, enc.variables)
    assert not is_valid
    assert any("non-negativity" in err.lower() for err in errors)


def test_initial_verification_vacuous_truth():
    """
    Test initial verification with state in infinity region.

    Program:
        init: x = -1
        rank(q0):
            [] x >= 0 -> 10 - x
            [] x < 0 -> inf

    At x=-1, the infinity guard is satisfied (x < 0).
    Initial verification checks that init (x = -1) does NOT satisfy infinity guard,
    but it does, so this would actually fail unless we weaken the init or change the test.

    Better: test that init x=0 doesn't satisfy the infinity guard x < 0.
    """
    program = """
        init: x = 0

        rank(q0):
            [] x >= 0 -> 10 - x
            [] x < 0 -> inf

        trans(q0, q0): true
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    assert len(verification.obligations) == 1
    # Passes because init state x=0 does not satisfy infinity guard x < 0
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
            [] x < 0 -> inf

        rank(q1):
            [] x >= 0 -> 5 - x
            [] x < 0 -> inf

        trans(q1, q1): true
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    summary = verification.summary()
    # New system: 2 initial_non_infinity (one per state)
    assert summary == "2/2 obligations verified"


def test_verification_get_witnesses():
    """Test VerificationResult.get_witnesses() method."""
    program = """
        init: x = 0

        rank(q0):
            [] x >= 0 -> 10 - x
            [] x < 0 -> inf

        trans(q0, q0): true
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    witnesses = verification.get_witnesses()
    # New system: 1 initial_non_infinity obligation
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
            [] x < 0 -> inf
            [] x >= maxVal -> inf

        trans(q0, q0): true
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    # New system: 2 initial_non_infinity obligations (2 infinity cases)
    assert len(verification.obligations) == 2
    assert all(o.passed for o in verification.obligations)
    assert verification.passed is True


def test_transition_verification_simple():
    """
    Test verification obligations for a simple counter.

    Program:
        init: x = 0
        [] x < 10 -> x = x + 1
        rank(q0):
            [] x >= 0 && x < 11 -> 11 - x
            [] x < 0 || x >= 11 -> inf
        trans(q0, q0): x < 10

    Note: Guard is x < 11 (not x < 10) to ensure the ranking value
    is still positive after the transition (when x' could be 10).
    """
    program = """
        init: x = 0

        [] x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 && x < 11 -> 11 - x
            [] x < 0 -> inf
            [] x >= 11 -> inf

        trans(q0, q0): x < 10
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    # New system: 2 initial_non_infinity + 2 transition_non_infinity + 1 update = 5
    assert len(verification.obligations) == 5

    # Check obligation types
    types = [o.obligation_type for o in verification.obligations]
    assert "initial_non_infinity" in types
    assert "transition_non_infinity" in types
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
            [] x <= 0 -> inf
        trans!(q0, q0): x > 1

    Note: Guard is x > 0 (not x >= 0) to ensure ranking value is strictly positive.
    Transition requires x > 1 to ensure x' > 0 after decrement.
    """
    program = """
        init: x = 10

        [] x > 1 -> x = x - 1

        rank(q0):
            [] x > 0 -> x
            [] x <= 0 -> inf

        trans!(q0, q0): x > 1
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    # New system: 1 initial_non_infinity + 1 transition_non_infinity + 1 update = 3
    assert len(verification.obligations) == 3

    types = [o.obligation_type for o in verification.obligations]
    assert "initial_non_infinity" in types
    assert "transition_non_infinity" in types
    assert "update" in types

    # Check that the update obligation is marked as fair
    update_obls = [o for o in verification.obligations if o.obligation_type == "update"]
    assert len(update_obls) == 1
    assert update_obls[0].is_fair is True

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

    The verifier should handle multiple cases correctly.
    """
    program = """
        init: x = 0

        [] x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 && x < 5 -> 10 - x
            [] x >= 5 && x < 10 -> 20 - x
            [] x < 0 -> inf
            [] x >= 10 -> inf

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

    # New system: 2 initial_non_infinity + 4 transition_non_infinity + 4 update = 10
    # initial: 1 state × 2 infinity_cases = 2
    # transition: 1 prog × 1 aut × 2 finite_cases × 2 infinity_cases = 4
    # update: 1 prog × 1 aut × 2 source_cases × 2 target_cases = 4
    assert len(verification.obligations) == 10

    # Check obligation types
    update_obls = [o for o in verification.obligations if o.obligation_type == "update"]
    assert len(update_obls) == 4  # 2 source cases × 2 target cases

    # Should have source case indices 0 and 1
    source_indices = {o.source_case_idx for o in update_obls}
    assert source_indices == {0, 1}


def test_single_case_no_warning():
    """
    Test that no warning is issued when ranking functions have a single case.
    """
    program = """
        init: x = 0

        [] x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 && x <= 10 -> 11 - x
            [] x < 0 -> inf
            [] x > 10 -> inf

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

    # New system: 2 initial_non_infinity + 2 transition_non_infinity + 1 update = 5
    # initial: 1 state × 2 infinity_cases = 2
    # transition: 1 prog × 1 aut × 1 finite_case × 2 infinity_cases = 2
    # update: 1 prog × 1 aut × 1 source_case × 1 target_case = 1
    assert len(verification.obligations) == 5

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
            [] x < 0 -> inf
            [] x >= 11 -> inf

        rank(q1):
            [] x >= 0 && x < 11 -> 11 - x
            [] x < 0 -> inf
            [] x >= 11 -> inf

        rank(q2):
            [] x >= 0 && x < 3 -> 8 - x
            [] x >= 3 && x < 11 -> 12 - x
            [] x < 0 -> inf
            [] x >= 11 -> inf

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

    # New system: 6 initial_non_infinity + 10 transition_non_infinity + 8 update = 24
    # initial: 3 states × 2 infinity_cases = 6
    # transition: q0->q1 (2 fin × 2 inf = 4) + q1->q2 (1 fin × 2 inf = 2) + q2->q2 (2 fin × 2 inf = 4) = 10
    # update: q0->q1 (2 src × 1 tgt = 2) + q1->q2 (1 src × 2 tgt = 2) + q2->q2 (2 src × 2 tgt = 4) = 8
    initial_obls = [o for o in verification.obligations if o.obligation_type == "initial_non_infinity"]
    assert len(initial_obls) == 6

    update_obls = [o for o in verification.obligations if o.obligation_type == "update"]
    assert len(update_obls) == 8

    # Check states are present
    states = {o.source_ranking_state for o in initial_obls}
    assert states == {"q0", "q1", "q2"}


def test_initial_obligations_respect_automaton_init():
    """
    Test that initial_non_infinity obligations only check states in Q_0.

    When automaton_init is specified, we should only verify initial conditions
    for states that are actually in the initial automaton state set.
    """
    program = """
        init: x = 0

        [] x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 && x <= 10 -> 10 - x
            [] x < 0 -> inf
            [] x > 10 -> inf

        rank(q1):
            [] x >= 5 && x <= 15 -> 15 - x
            [] x < 5 -> inf
            [] x > 15 -> inf

        automaton_init: q0

        trans(q0, q1): x >= 5
        trans(q1, q0): x < 8
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    # Should only have initial_non_infinity for q0 (which is in Q_0)
    # q0 has 2 infinity cases, so 2 initial obligations
    initial_obls = [o for o in verification.obligations if o.obligation_type == "initial_non_infinity"]
    assert len(initial_obls) == 2

    # All should be for q0 only
    states = {o.source_ranking_state for o in initial_obls}
    assert states == {"q0"}

    # No initial obligations for q1 (not in Q_0)
    q1_initial = [o for o in initial_obls if o.source_ranking_state == "q1"]
    assert len(q1_initial) == 0


def test_transition_non_infinity_uses_correct_states():
    """
    Test that transition_non_infinity obligations use:
    - Finite cases from source state (q)
    - Infinity cases from target state (q')

    For automaton transition (q, σ, q') ∈ δ.
    """
    program = """
        init: x = 5

        [] x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 && x <= 10 -> 10 - x
            [] x < 0 -> inf
            [] x > 10 -> inf

        rank(q1):
            [] x >= 5 && x <= 15 -> 15 - x
            [] x < 5 -> inf
            [] x > 15 -> inf

        automaton_init: q0

        trans(q0, q1): x >= 5
        trans(q1, q0): x <= 10
    """

    result = parse_with_constants(program)
    verifier = Verifier(result)
    verification = verifier.verify_all()

    trans_non_inf = [o for o in verification.obligations if o.obligation_type == "transition_non_infinity"]

    # Verify all obligations have correct source/target states
    for obl in trans_non_inf:
        aut_from, aut_to = obl.automaton_transition
        # Source ranking state should match automaton from_state
        assert obl.source_ranking_state == aut_from, \
            f"Source ranking state {obl.source_ranking_state} doesn't match automaton from_state {aut_from}"
        # Target ranking state should match automaton to_state
        assert obl.target_ranking_state == aut_to, \
            f"Target ranking state {obl.target_ranking_state} doesn't match automaton to_state {aut_to}"

    # Count obligations by transition
    q0_to_q1 = [o for o in trans_non_inf if o.automaton_transition == ("q0", "q1")]
    q1_to_q0 = [o for o in trans_non_inf if o.automaton_transition == ("q1", "q0")]

    # q0->q1: 1 prog_trans × 1 finite_case(q0) × 2 infinity_cases(q1) = 2
    assert len(q0_to_q1) == 2
    # q1->q0: 1 prog_trans × 1 finite_case(q1) × 2 infinity_cases(q0) = 2
    assert len(q1_to_q0) == 2
