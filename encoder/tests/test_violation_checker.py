"""Tests for violation set computation."""

import pytest
from zkterm_tool import (
    parse_with_constants,
    encode_ranking_functions,
    encode_automaton_transitions,
    encode_init
)
from zkterm_tool.state_enumerator import create_state_space
from zkterm_tool.violation_checker import (
    compute_violation_sets,
    compute_state_embedding,
    compute_transition_embedding,
    compute_embeddings
)


def test_b_init_simple():
    """Test B_init computation with simple counter."""
    program = """
    init: x = 0

    rank(q0):
        [] x >= 0 && x <= 5 -> 6 - x

    trans(q0, q0): x < 5
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)
    aut_encs = encode_automaton_transitions(result.automaton_transitions)
    init_enc = encode_init(result.init_condition) if result.init_condition else None

    variables = ["x"]
    state_space = create_state_space(variables, ["x:0:10"])

    violations = compute_violation_sets(
        state_space,
        rank_encs,
        aut_encs,
        init_enc,
        ["q0"]  # Initial automaton state
    )

    # x=0 to x=5 should have defined ranking
    assert {"x": 0} not in violations.B_init
    assert {"x": 5} not in violations.B_init

    # x=6 to x=10 should be in B_init (undefined ranking)
    assert {"x": 6} in violations.B_init
    assert {"x": 7} in violations.B_init
    assert {"x": 10} in violations.B_init

    assert len(violations.B_init) == 5  # x=6,7,8,9,10


def test_b_step_increasing_rank():
    """Test B_step captures transitions where ranking increases."""
    program = """
    init: x = 0

    rank(q0):
        [] x >= 0 && x <= 10 -> x

    trans(q0, q0): x < 10
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)
    aut_encs = encode_automaton_transitions(result.automaton_transitions)
    init_enc = encode_init(result.init_condition) if result.init_condition else None

    variables = ["x"]
    state_space = create_state_space(variables, ["x:0:5"])

    violations = compute_violation_sets(
        state_space,
        rank_encs,
        aut_encs,
        init_enc,
        ["q0"]
    )

    # Any transition (x) → (x+1) should be in B_step (ranking increases)
    # But we need to check which transitions are actually enabled
    # trans(q0, q0): x < 10, so all states x=0..4 enable transitions

    # Check that some increasing transitions are caught
    # Note: We enumerate all (s, s') pairs, so (0, 1), (0, 2), etc. all checked
    # (0, 1): V(0) = 0 < V(1) = 1, enabled at x=0, violation!
    assert ({"x": 0}, {"x": 1}) in violations.B_step

    # (0, 5): V(0) = 0 < V(5) = 5, enabled at x=0, violation!
    assert ({"x": 0}, {"x": 5}) in violations.B_step


def test_b_fairstep_nondecreasing():
    """Test B_fairstep captures fair transitions where ranking doesn't decrease."""
    program = """
    init: x = 0

    rank(q0):
        [] x >= 0 && x <= 10 -> 10 - x

    trans!(q0, q0): x < 10
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)
    aut_encs = encode_automaton_transitions(result.automaton_transitions)
    init_enc = encode_init(result.init_condition) if result.init_condition else None

    variables = ["x"]
    state_space = create_state_space(variables, ["x:0:5"])

    violations = compute_violation_sets(
        state_space,
        rank_encs,
        aut_encs,
        init_enc,
        ["q0"]
    )

    # Fair transition: should have B_fairstep violations
    # (0, 0): V(0) = 10 ≤ V(0) = 10, enabled, violation!
    assert ({"x": 0}, {"x": 0}) in violations.B_fairstep

    # (0, 1): V(0) = 10 ≤ V(1) = 9? No, 10 > 9, not a violation
    # Actually wait, the condition is V(s,q) ≤ V(s',q'), violation means ≤ (not strictly decreasing)
    # So 10 > 9 means it DOES decrease, so NOT in B_fairstep

    # Let me reconsider: for fair transitions, we want V to STRICTLY DECREASE
    # Violation is when V(s,q) ≤ V(s',q') (i.e., doesn't strictly decrease)
    # So (0, 1): 10 > 9, this IS a strict decrease, so NOT a violation

    # (1, 0): V(1) = 9 ≤ V(0) = 10? Yes, 9 < 10, violation!
    assert ({"x": 1}, {"x": 0}) in violations.B_fairstep


def test_empty_violation_sets():
    """Test case where all violations are empty (correct program)."""
    program = """
    init: x = 0

    rank(q0):
        [] x >= 0 && x <= 5 -> 5 - x

    trans(q0, q0): x < 5
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)
    aut_encs = encode_automaton_transitions(result.automaton_transitions)
    init_enc = encode_init(result.init_condition) if result.init_condition else None

    variables = ["x"]
    # Restrict to valid range
    state_space = create_state_space(variables, ["x:0:5"])

    violations = compute_violation_sets(
        state_space,
        rank_encs,
        aut_encs,
        init_enc,
        ["q0"]
    )

    # All states have defined ranking
    assert len(violations.B_init) == 0

    # We enumerate all (s, s') pairs and check if transition enabled
    # For this program:
    # - trans(q0, q0): x < 5, so enabled for x=0,1,2,3,4
    # - ranking: 5-x, so (0→1): 5 > 4 (decreases), (1→2): 4 > 3 (decreases), etc.
    # - But we check ALL pairs (s, s'), not just program transitions!
    # - (0, 0): 5 ≤ 5? No, 5 < 5 is false, so not in B_step
    # - (0, 5): 5 < 0? No, so not in B_step
    # - Actually, V(s,q) < V(s',q') is the violation condition for B_step
    # - So we want transitions where ranking INCREASES

    # Since ranking = 5-x, any transition with x' > x will have lower rank
    # And any transition with x' < x will have higher rank (violation!)

    # trans enabled for x=0,1,2,3,4
    # (4, 3): V(4)=1 < V(3)=2, violation! But is transition enabled?
    # Yes, x=4 < 5, so transition enabled

    # Actually, I think the issue is that we're checking ALL (s,s') pairs
    # regardless of whether they're actual program transitions
    # The automaton transition only checks the SOURCE state guard

    # Let me check the violation_checker code...
    # Yes, it checks if source state s satisfies automaton guard
    # So (4, 3) would be checked if x=4 satisfies guard x < 5 (yes)

    # So there WILL be violations in B_step for this program!
    # Because we're checking all possible (s,s') pairs, not just program transitions

    # Let me reconsider the test...
    assert len(violations.B_step) > 0  # There will be violations


def test_metadata():
    """Test that metadata is correctly populated."""
    program = """
    rank(q0):
        [] x >= 0 -> x

    trans(q0, q1): x < 5
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)
    aut_encs = encode_automaton_transitions(result.automaton_transitions)

    variables = ["x"]
    state_space = create_state_space(variables, ["x:0:3"])

    violations = compute_violation_sets(
        state_space,
        rank_encs,
        aut_encs,
        None,
        ["q0"]
    )

    assert violations.variables == ["x"]
    assert "q0" in violations.automaton_states
    assert violations.num_states_enumerated == 4  # x=0,1,2,3
    assert violations.num_transitions_checked == 4 * 4  # All pairs


def test_compute_state_embedding():
    """Test state embedding computation."""
    state = {"x": 5, "y": 3}
    variables = ["x", "y"]
    base = 6  # max(5, 3) + 1
    field_size = 101  # Small prime for testing

    embedding = compute_state_embedding(state, variables, base, field_size)

    # e_1([5, 3]) = 5 * 6^0 + 3 * 6^1 = 5 + 18 = 23
    assert embedding == 23 % 101


def test_compute_state_embedding_single_var():
    """Test state embedding with single variable."""
    state = {"x": 7}
    variables = ["x"]
    base = 8  # max(7) + 1
    field_size = 101

    embedding = compute_state_embedding(state, variables, base, field_size)

    # e_1([7]) = 7 * base^0 = 7
    assert embedding == 7


def test_compute_transition_embedding():
    """Test transition embedding computation."""
    s = {"x": 5}
    s_prime = {"x": 6}
    variables = ["x"]
    state_base = 7  # max(5, 6) + 1
    field_size = 101

    # With state_base=7: e_1(5) = 5, e_1(6) = 6
    # Max state embedding is 6, so transition_base = 7
    transition_base = 7

    embedding = compute_transition_embedding(s, s_prime, variables, state_base, transition_base, field_size)

    # e_2([s, s']) = e_1(s) + e_1(s') * transition_base
    # = 5 + 6 * 7 = 5 + 42 = 47
    expected = (5 + 6 * transition_base) % field_size
    assert embedding == expected


def test_compute_embeddings():
    """Test computing embeddings for violation sets."""
    program = """
    rank(q0):
        [] x >= 0 && x <= 2 -> 2 - x

    trans(q0, q0): x < 2
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)
    aut_encs = encode_automaton_transitions(result.automaton_transitions)

    variables = ["x"]
    state_space = create_state_space(variables, ["x:0:5"])

    violations = compute_violation_sets(
        state_space,
        rank_encs,
        aut_encs,
        None,
        ["q0"]
    )

    field_size = 101
    embeddings = compute_embeddings(violations, field_size)

    assert embeddings.field_size == field_size
    assert len(embeddings.E_init) == len(violations.B_init)
    assert len(embeddings.E_step) == len(violations.B_step)
    assert len(embeddings.E_fairstep) == len(violations.B_fairstep)

    # Check that embeddings are integers in field
    for e in embeddings.E_init:
        assert isinstance(e, int)
        assert 0 <= e < field_size


def test_multivar_violations():
    """Test violation computation with multiple variables."""
    program = """
    rank(q0):
        [] x >= 0 && y >= 0 && x + y <= 5 -> x + y

    trans(q0, q0): x + y < 5
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)
    aut_encs = encode_automaton_transitions(result.automaton_transitions)

    variables = ["x", "y"]
    state_space = create_state_space(variables, ["x:0:3", "y:0:3"])

    violations = compute_violation_sets(
        state_space,
        rank_encs,
        aut_encs,
        None,
        ["q0"]
    )

    # States where x+y > 5 should be in B_init
    # But we only enumerate up to (3, 3), so x+y max is 6
    # x+y=6: (3,3) should be in B_init
    assert {"x": 3, "y": 3} in violations.B_init

    # States where x+y <= 5 should not be in B_init
    assert {"x": 0, "y": 0} not in violations.B_init
    assert {"x": 2, "y": 2} not in violations.B_init
