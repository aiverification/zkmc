"""Encode Büchi automaton transitions as matrix/vector forms.

Given automaton transitions:
    trans(q, q'): guard     - Regular transition (δ only)
    trans!(q, q'): guard    - Fair transition (both δ and F)

We encode:
    - δ (all transitions): A^(q,q')_δ x ≤ b^(q,q')_δ
    - F (fair transitions): A^(q,q')_F x ≤ b^(q,q')_F

Where x = [var1, var2, ...] contains current-state variables only.
"""

from dataclasses import dataclass
from typing import List
import numpy as np
from numpy.typing import NDArray

from .automaton_types import AutomatonTransition
from .encoder import comparison_to_inequalities, Inequality


@dataclass
class AutomatonTransitionEncoding:
    """Encoding of one Büchi automaton transition.

    Contains:
    - δ encoding (always present): A_delta x ≤ b_delta
    - F encoding (only if is_fair): A_fair x ≤ b_fair
    """
    from_state: str
    to_state: str
    variables: List[str]              # Variable ordering
    A_delta: NDArray[np.int64]        # δ matrix (always present)
    b_delta: NDArray[np.int64]        # δ vector (always present)
    A_fair: NDArray[np.int64] | None  # F matrix (only if is_fair)
    b_fair: NDArray[np.int64] | None  # F vector (only if is_fair)
    is_fair: bool                     # True if marked with !

    def __repr__(self) -> str:
        fair_str = " (FAIR)" if self.is_fair else ""
        lines = [f"Transition: {self.from_state} -> {self.to_state}{fair_str}"]
        lines.append(f"Variables: {self.variables}")

        # δ encoding (always present)
        lines.append(f"\nδ encoding A^({self.from_state},{self.to_state}) x ≤ b:")
        if self.A_delta.shape[0] > 0:
            lines.append(f"  A =\n{self.A_delta}")
            lines.append(f"  b = {self.b_delta}")
        else:
            lines.append("  (no constraints - always true)")

        # F encoding (only for fair transitions)
        if self.is_fair and self.A_fair is not None:
            lines.append(f"\nF encoding A^({self.from_state},{self.to_state}) x ≤ b:")
            if self.A_fair.shape[0] > 0:
                lines.append(f"  A =\n{self.A_fair}")
                lines.append(f"  b = {self.b_fair}")

        return "\n".join(lines)


def encode_automaton_transition(
    trans: AutomatonTransition,
    variables: List[str] | None = None
) -> AutomatonTransitionEncoding:
    """Encode one automaton transition to matrices.

    Args:
        trans: The automaton transition to encode
        variables: Optional ordered list of variables. If None, extracted from transition.

    Returns:
        AutomatonTransitionEncoding with δ and optionally F matrices
    """
    # Extract variables if not provided
    if variables is None:
        variables = sorted(trans.get_variables())

    # Encode guards to inequalities (no primed variables)
    all_ineqs: List[Inequality] = []
    for guard in trans.guards:
        all_ineqs.extend(comparison_to_inequalities(guard, primed=False))

    # Convert to non-strict inequalities only
    all_ineqs = [iq.to_nonstrict() for iq in all_ineqs]

    # Build variable index
    var_idx = {v: i for i, v in enumerate(variables)}
    n_vars = len(variables)

    # Build matrices
    if all_ineqs:
        m = len(all_ineqs)
        A_delta = np.zeros((m, n_vars), dtype=np.int64)
        b_delta = np.zeros(m, dtype=np.int64)

        for i, iq in enumerate(all_ineqs):
            for v, coeff in iq.coeffs.items():
                if v in var_idx:
                    A_delta[i, var_idx[v]] = coeff
            b_delta[i] = iq.const
    else:
        # No constraints (always true)
        A_delta = np.zeros((0, n_vars), dtype=np.int64)
        b_delta = np.zeros(0, dtype=np.int64)

    # For fair transitions, F encoding is same as δ encoding
    if trans.is_fair:
        A_fair = A_delta.copy()
        b_fair = b_delta.copy()
    else:
        A_fair = None
        b_fair = None

    return AutomatonTransitionEncoding(
        from_state=trans.from_state,
        to_state=trans.to_state,
        variables=variables,
        A_delta=A_delta,
        b_delta=b_delta,
        A_fair=A_fair,
        b_fair=b_fair,
        is_fair=trans.is_fair
    )


def encode_automaton_transitions(
    transitions: List[AutomatonTransition]
) -> List[AutomatonTransitionEncoding]:
    """Encode multiple automaton transitions with consistent variable ordering.

    Args:
        transitions: List of automaton transitions to encode

    Returns:
        List of AutomatonTransitionEncoding objects
    """
    if not transitions:
        return []

    # Collect all variables from all transitions
    all_vars: set[str] = set()
    for trans in transitions:
        all_vars.update(trans.get_variables())

    variables = sorted(all_vars)

    # Encode each transition with the same variable ordering
    return [encode_automaton_transition(trans, variables) for trans in transitions]
