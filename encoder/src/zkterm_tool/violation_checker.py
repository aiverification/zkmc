"""Violation set computation for explicit-state verification.

This module computes the three violation sets (B_init, B_step, B_fairstep),
the valid sets (S, S0, T), verification checks, and optionally computes
field embeddings for polynomial commitment schemes.
"""

from typing import Dict, List, Tuple, Any
from dataclasses import dataclass
import numpy as np
from .state_enumerator import StateSpace
from .ranking_evaluator import evaluate_ranking, check_automaton_guard, check_guard


@dataclass
class ViolationSets:
    """The three violation sets and valid sets for explicit-state verification.

    Attributes:
        B_init: States where V(s,q) = ∞ for some q ∈ Q_0
        B_step: Transitions where V increases on regular transitions
        B_fairstep: Fair transitions where V doesn't strictly decrease
        S: Complete state space (all enumerated states)
        S0: Initial states (states satisfying init condition)
        T: Transition relation (valid program transitions)
        SxS: All possible transitions (Cartesian product S × S)
        variables: List of variable names
        automaton_states: List of automaton state names
        num_states_enumerated: Total number of states enumerated
        num_transitions_checked: Total number of transitions checked
    """
    B_init: List[Dict[str, int]]
    B_step: List[Tuple[Dict[str, int], Dict[str, int]]]
    B_fairstep: List[Tuple[Dict[str, int], Dict[str, int]]]
    S: List[Dict[str, int]]
    S0: List[Dict[str, int]]
    T: List[Tuple[Dict[str, int], Dict[str, int]]]
    SxS: List[Tuple[Dict[str, int], Dict[str, int]]]
    variables: List[str]
    automaton_states: List[str]
    num_states_enumerated: int
    num_transitions_checked: int


@dataclass
class VerificationChecks:
    """Results of disjointness verification for ZK proofs.

    These checks verify that the violation sets are disjoint from the
    valid sets, which is the property the ZK prover must prove.

    Attributes:
        init_disjoint: True if S_0 ∩ B_init = ∅
        step_disjoint: True if T ∩ B_step = ∅
        fairstep_disjoint: True if T ∩ B_fairstep = ∅
        all_disjoint: True if all three intersections are empty
        init_intersection_size: |S_0 ∩ B_init|
        step_intersection_size: |T ∩ B_step|
        fairstep_intersection_size: |T ∩ B_fairstep|
    """
    init_disjoint: bool
    step_disjoint: bool
    fairstep_disjoint: bool
    all_disjoint: bool
    init_intersection_size: int
    step_intersection_size: int
    fairstep_intersection_size: int


def compute_initial_states(
    all_states: List[Dict[str, int]],
    state_space: StateSpace,
    init_enc: Any
) -> List[Dict[str, int]]:
    """Compute S0: states satisfying the initial condition.

    Args:
        all_states: List of all states in state space
        state_space: StateSpace for vector conversion
        init_enc: InitEncoding with A_0 and b_0 matrices

    Returns:
        List of states satisfying init condition

    Note:
        If init_enc is None, all states are considered initial.
    """
    if init_enc is None:
        # No init condition, all states are initial
        return list(all_states)

    S0 = []
    for s in all_states:
        s_vec = state_space.state_to_vector(s)
        if check_guard(s_vec, init_enc.A_0, init_enc.b_0):
            S0.append(s)

    return S0


def compute_transition_relation(
    all_states: List[Dict[str, int]],
    state_space: StateSpace,
    trans_encs: List[Any]
) -> List[Tuple[Dict[str, int], Dict[str, int]]]:
    """Compute T: the transition relation (valid program transitions).

    Args:
        all_states: List of all states in state space
        state_space: StateSpace for vector conversion
        trans_encs: List of TransitionEncoding objects for program transitions

    Returns:
        List of (s, s') pairs where (s, s') ∈ T

    Note:
        A transition (s, s') is in T if there exists a program transition
        whose guard and assignments are satisfied by the concrete state pair.
    """
    T = []

    for s in all_states:
        for s_prime in all_states:
            # Create combined vector [s, s'] for transition space
            s_vec = state_space.state_to_vector(s)
            s_prime_vec = state_space.state_to_vector(s_prime)
            combined = np.concatenate([s_vec, s_prime_vec])

            # Check if any program transition accepts this (s, s') pair
            for trans_enc in trans_encs:
                # Check if combined vector satisfies A [s, s'] ≤ b
                if check_guard(combined, trans_enc.A, trans_enc.b):
                    T.append((s, s_prime))
                    break  # Found one transition, no need to check others

    return T


def verify_disjointness(
    violations: ViolationSets
) -> VerificationChecks:
    """Verify that violation sets are disjoint from valid sets.

    This checks the three properties that the ZK prover must prove:
    - S_0 ∩ B_init = ∅
    - T ∩ B_step = ∅
    - T ∩ B_fairstep = ∅

    Args:
        violations: ViolationSets containing both violation and valid sets

    Returns:
        VerificationChecks with disjointness results

    Example:
        >>> checks = verify_disjointness(violations)
        >>> if checks.all_disjoint:
        ...     print("All verification checks passed!")
    """
    # Convert to sets for intersection (use tuples of sorted items for hashability)
    def state_to_tuple(s: Dict[str, int]) -> Tuple:
        return tuple(sorted(s.items()))

    def transition_to_tuple(t: Tuple[Dict[str, int], Dict[str, int]]) -> Tuple:
        return (state_to_tuple(t[0]), state_to_tuple(t[1]))

    # Convert to sets
    B_init_set = set(state_to_tuple(s) for s in violations.B_init)
    S0_set = set(state_to_tuple(s) for s in violations.S0)

    B_step_set = set(transition_to_tuple(t) for t in violations.B_step)
    T_set = set(transition_to_tuple(t) for t in violations.T)

    B_fairstep_set = set(transition_to_tuple(t) for t in violations.B_fairstep)

    # Compute intersections
    init_intersection = B_init_set & S0_set
    step_intersection = B_step_set & T_set
    fairstep_intersection = B_fairstep_set & T_set

    return VerificationChecks(
        init_disjoint=len(init_intersection) == 0,
        step_disjoint=len(step_intersection) == 0,
        fairstep_disjoint=len(fairstep_intersection) == 0,
        all_disjoint=(
            len(init_intersection) == 0 and
            len(step_intersection) == 0 and
            len(fairstep_intersection) == 0
        ),
        init_intersection_size=len(init_intersection),
        step_intersection_size=len(step_intersection),
        fairstep_intersection_size=len(fairstep_intersection)
    )


@dataclass
class FieldEmbeddings:
    """Field embeddings for polynomial commitment schemes.

    These embeddings provide injective mappings from states and transitions
    to field elements for use in zero-knowledge proofs based on polynomial
    commitments (e.g., KZG).

    Attributes:
        E_init: Embeddings e_1(s) for s ∈ B_init
        E_step: Embeddings e_2(s,s') for (s,s') ∈ B_step
        E_fairstep: Embeddings e_2(s,s') for (s,s') ∈ B_fairstep
        E_S: Embeddings e_1(s) for s ∈ S (complete state space)
        E_S0: Embeddings e_1(s) for s ∈ S0 (initial states)
        E_T: Embeddings e_2(s,s') for (s,s') ∈ T (program transitions)
        E_SxS: Embeddings e_2(s,s') for (s,s') ∈ S×S (all possible transitions)
        field_size: Size of field F (should be prime)
        max_embedding: Maximum embedding value (for field size check)
        embeddings_valid: True if all embeddings fit in field
    """
    E_init: List[int]
    E_step: List[int]
    E_fairstep: List[int]
    E_S: List[int]
    E_S0: List[int]
    E_T: List[int]
    E_SxS: List[int]
    field_size: int
    max_embedding: int
    embeddings_valid: bool


def compute_violation_sets(
    state_space: StateSpace,
    rank_encs: Dict[str, Any],
    aut_encs: List[Any],
    init_enc: Any,
    automaton_initial_states: List[str],
    trans_encs: List[Any] = None
) -> ViolationSets:
    """Compute violation sets (B_init, B_step, B_fairstep) and valid sets (S, S0, T, SxS).

    This function enumerates all states in the state space and checks which
    ones violate the termination obligations by contraposition. It also
    computes the valid sets needed for polynomial construction in ZK proofs.

    Args:
        state_space: StateSpace with bounds for enumeration
        rank_encs: Ranking function encodings by automaton state
        aut_encs: List of automaton transition encodings
        init_enc: Initial condition encoding
        automaton_initial_states: Initial states of the automaton (Q_0)
        trans_encs: List of program transition encodings (for computing T)

    Returns:
        ViolationSets object containing violation sets (B_init, B_step, B_fairstep)
        and valid sets (S, S0, T, SxS)

    Mathematical Background:
        Violation sets (bad sets):
        - B_init = {s | ∃q ∈ Q_0: V(s,q) = ∞}
        - B_step = {(s,s') | ∃q,q': V(s,q) ≠ ∞ ∧ (q,⟦s⟧,q') ∈ δ ∧ V(s,q) < V(s',q')}
        - B_fairstep = {(s,s') | ∃q,q': V(s,q) ≠ ∞ ∧ (q,⟦s⟧,q') ∈ F ∧ V(s,q) ≤ V(s',q')}

        Valid sets:
        - S = complete state space (all enumerated states)
        - S0 = initial states satisfying init condition
        - T = program transition relation
        - SxS = all possible transitions (Cartesian product S × S)

    Note:
        The prover must prove that S_0 ∩ B_init = ∅, T ∩ B_step = ∅,
        and T ∩ B_fairstep = ∅.
    """
    B_init = []
    B_step = []
    B_fairstep = []

    num_states = 0
    num_transitions = 0

    # Enumerate all states
    all_states = list(state_space.enumerate_states())
    num_states = len(all_states)

    # 1. Compute B_init: states where V(s,q) = ∞ for some q ∈ Q_0
    for state in all_states:
        for q in automaton_initial_states:
            if q not in rank_encs:
                # No ranking function for this state, treat as ∞
                B_init.append(state)
                break

            rank_enc = rank_encs[q]
            value = evaluate_ranking(state, rank_enc)

            if value is None:  # V(s,q) = ∞
                B_init.append(state)
                break

    # 2. Compute B_step and B_fairstep: transition violations
    # For each pair of states (s, s')
    for s in all_states:
        for s_prime in all_states:
            num_transitions += 1

            # Check all automaton transitions
            for aut_enc in aut_encs:
                q = aut_enc.from_state
                q_prime = aut_enc.to_state

                # Skip if no ranking functions defined
                if q not in rank_encs or q_prime not in rank_encs:
                    continue

                # Check if transition is enabled: (q, ⟦s⟧, q') ∈ δ
                if not check_automaton_guard(s, aut_enc):
                    continue  # Transition not enabled

                # Evaluate ranking values
                V_s_q = evaluate_ranking(s, rank_encs[q])
                V_s_prime_q_prime = evaluate_ranking(s_prime, rank_encs[q_prime])

                # Skip if V(s,q) = ∞ (premise not satisfied)
                if V_s_q is None:
                    continue

                # Check violations based on transition type
                if aut_enc.is_fair:
                    # Fair transition: check if V(s,q) ≤ V(s',q')
                    # Violation means V doesn't strictly decrease
                    if V_s_prime_q_prime is None or V_s_q <= V_s_prime_q_prime:
                        # Violation: doesn't strictly decrease
                        B_fairstep.append((s, s_prime))
                else:
                    # Regular transition: check if V(s,q) < V(s',q')
                    # Violation means V increases
                    if V_s_prime_q_prime is None or V_s_q < V_s_prime_q_prime:
                        # Violation: increases
                        B_step.append((s, s_prime))

    # 3. Compute valid sets: S, S0, T, SxS
    S = list(all_states)  # Complete state space
    S0 = compute_initial_states(all_states, state_space, init_enc)
    T = compute_transition_relation(all_states, state_space, trans_encs) if trans_encs else []

    # SxS: Cartesian product S × S (all possible transitions)
    import itertools
    SxS = [(s, s_prime) for s, s_prime in itertools.product(all_states, all_states)]

    # Helper functions for sorting
    def state_to_tuple(s: Dict[str, int]) -> Tuple:
        """Convert state dict to tuple for sorting."""
        return tuple(s[var] for var in state_space.variables)

    def transition_to_tuple(t: Tuple[Dict[str, int], Dict[str, int]]) -> Tuple:
        """Convert transition to tuple for sorting."""
        return (state_to_tuple(t[0]), state_to_tuple(t[1]))

    # Sort all sets
    B_init.sort(key=state_to_tuple)
    B_step.sort(key=transition_to_tuple)
    B_fairstep.sort(key=transition_to_tuple)
    S.sort(key=state_to_tuple)
    S0.sort(key=state_to_tuple)
    T.sort(key=transition_to_tuple)
    SxS.sort(key=transition_to_tuple)

    return ViolationSets(
        B_init=B_init,
        B_step=B_step,
        B_fairstep=B_fairstep,
        S=S,
        S0=S0,
        T=T,
        SxS=SxS,
        variables=state_space.variables,
        automaton_states=list(rank_encs.keys()),
        num_states_enumerated=num_states,
        num_transitions_checked=num_transitions
    )


def compute_state_embedding(
    state: Dict[str, int],
    variables: List[str],
    base: int,
    field_size: int
) -> int:
    """Compute embedding e_1: S → F for a state.

    Uses a simple injective mapping based on polynomial evaluation:
    e_1([v_1, v_2, ..., v_n]) = ∑_i v_i * base^i mod field_size

    Args:
        state: State dictionary {'var': value, ...}
        variables: Ordered variable list
        base: Base for polynomial evaluation (must be > max variable value)
        field_size: Size of field (should be prime)

    Returns:
        Field element (integer in [0, field_size))

    Example:
        >>> compute_state_embedding({'x': 5, 'y': 3}, ['x', 'y'], 10, 2**256-189)
        # Returns field element representing state [5, 3]
    """
    # Convert to vector
    values = [state[var] for var in variables]

    # Compute polynomial evaluation: ∑ v_i * base^i
    result = 0
    for i, v in enumerate(values):
        result += v * (base ** i)

    return result % field_size


def compute_transition_embedding(
    s: Dict[str, int],
    s_prime: Dict[str, int],
    variables: List[str],
    state_base: int,
    transition_base: int,
    field_size: int
) -> int:
    """Compute embedding e_2: S × S → F for a transition.

    Uses a simple injective mapping based on state embeddings:
    e_2([s, s']) = e_1(s) + e_1(s') * transition_base

    Args:
        s: Source state
        s_prime: Target state
        variables: Ordered variable list
        state_base: Base for state embedding
        transition_base: Base for combining state embeddings (must be > max state embedding)
        field_size: Size of field

    Returns:
        Field element (integer in [0, field_size))

    Example:
        >>> compute_transition_embedding(
        ...     {'x': 5}, {'x': 6}, ['x'], 10, 100, 2**256-189)
        # Returns field element representing transition (5 → 6)
    """
    e_s = compute_state_embedding(s, variables, state_base, field_size)
    e_s_prime = compute_state_embedding(s_prime, variables, state_base, field_size)

    # Combine: e_2(s, s') = e_1(s) + e_1(s') * transition_base
    return (e_s + e_s_prime * transition_base) % field_size


def compute_embeddings(
    violations: ViolationSets,
    field_size: int = 52435875175126190479447740508185965837690552500527637822603658699938581184513
) -> FieldEmbeddings:
    """Compute field embeddings for all violation and valid sets.

    Args:
        violations: ViolationSets object containing both violation and valid sets
        field_size: Prime field size (default: BLS12-381 scalar field)

    Returns:
        FieldEmbeddings object with embeddings for all sets (B_init, B_step,
        B_fairstep, S, S0, T, SxS) plus validity check

    Note:
        The default field size is the BLS12-381 scalar field modulus
        (52435875175126190479447740508185965837690552500527637822603658699938581184513),
        commonly used in pairing-based cryptography and zkSNARK systems.

    Example:
        >>> embeddings = compute_embeddings(violations)
        >>> if not embeddings.embeddings_valid:
        ...     print(f"Warning: max embedding {embeddings.max_embedding} exceeds field size")
    """
    # Compute bases for injective embeddings
    # State base: larger than any variable value in the state space
    max_var_value = max(max(s[var] for s in violations.S) for var in violations.variables) if violations.S else 0
    state_base = max_var_value + 1

    # Compute all state embeddings first to find the maximum
    temp_state_embeddings = [
        compute_state_embedding(s, violations.variables, state_base, field_size)
        for s in violations.S
    ]
    max_state_embedding = max(temp_state_embeddings) if temp_state_embeddings else 0

    # Transition base: larger than any state embedding
    transition_base = max_state_embedding + 1

    # Now compute embeddings for all sets
    # Violation sets
    E_init = [
        compute_state_embedding(s, violations.variables, state_base, field_size)
        for s in violations.B_init
    ]

    E_step = [
        compute_transition_embedding(s, s_prime, violations.variables, state_base, transition_base, field_size)
        for s, s_prime in violations.B_step
    ]

    E_fairstep = [
        compute_transition_embedding(s, s_prime, violations.variables, state_base, transition_base, field_size)
        for s, s_prime in violations.B_fairstep
    ]

    # Valid sets
    E_S = temp_state_embeddings  # Reuse already computed embeddings

    E_S0 = [
        compute_state_embedding(s, violations.variables, state_base, field_size)
        for s in violations.S0
    ]

    E_T = [
        compute_transition_embedding(s, s_prime, violations.variables, state_base, transition_base, field_size)
        for s, s_prime in violations.T
    ]

    E_SxS = [
        compute_transition_embedding(s, s_prime, violations.variables, state_base, transition_base, field_size)
        for s, s_prime in violations.SxS
    ]

    # Find max embedding and check validity
    all_embeddings = E_init + E_step + E_fairstep + E_S + E_S0 + E_T + E_SxS
    max_embedding = max(all_embeddings) if all_embeddings else 0
    embeddings_valid = max_embedding < field_size

    return FieldEmbeddings(
        E_init=E_init,
        E_step=E_step,
        E_fairstep=E_fairstep,
        E_S=E_S,
        E_S0=E_S0,
        E_T=E_T,
        E_SxS=E_SxS,
        field_size=field_size,
        max_embedding=max_embedding,
        embeddings_valid=embeddings_valid
    )
