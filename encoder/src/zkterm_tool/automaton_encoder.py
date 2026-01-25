"""Encode Büchi automaton transitions as matrix/vector forms.

Given automaton transitions:
    trans(q, q'): guard     - Regular transition (δ only)
    trans!(q, q'): guard    - Fair transition (both δ and F)

We encode using paper notation P^(σ) x ≤ r^(σ):
    - All transitions: P^(q,q') x ≤ r^(q,q')
    - is_fair flag indicates if this is also in F set

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

    Paper notation: P^(σ) x ≤ r^(σ) where σ = (q, q')

    The is_fair flag indicates if this transition is in the F set.
    """
    from_state: str
    to_state: str
    variables: List[str]       # Variable ordering
    P: NDArray[np.int64]       # Guard matrix: P^(σ) x ≤ r^(σ)
    r: NDArray[np.int64]       # Guard vector
    is_fair: bool              # True if marked with ! (in F set)

    def __repr__(self) -> str:
        fair_str = " (FAIR)" if self.is_fair else ""
        lines = [f"Transition: {self.from_state} -> {self.to_state}{fair_str}"]
        lines.append(f"Variables: {self.variables}")

        # Encoding P^(σ) x ≤ r^(σ)
        lines.append(f"\nP^({self.from_state},{self.to_state}) x ≤ r^({self.from_state},{self.to_state}):")
        if self.P.shape[0] > 0:
            lines.append(f"  P =\n{self.P}")
            lines.append(f"  r = {self.r}")
        else:
            lines.append("  (no constraints - always true)")

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
        AutomatonTransitionEncoding with P^(σ) x ≤ r^(σ) and is_fair flag
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

    # Build matrices P^(σ) x ≤ r^(σ)
    if all_ineqs:
        m = len(all_ineqs)
        P = np.zeros((m, n_vars), dtype=np.int64)
        r = np.zeros(m, dtype=np.int64)

        for i, iq in enumerate(all_ineqs):
            for v, coeff in iq.coeffs.items():
                if v in var_idx:
                    P[i, var_idx[v]] = coeff
            r[i] = iq.const
    else:
        # No constraints (always true)
        P = np.zeros((0, n_vars), dtype=np.int64)
        r = np.zeros(0, dtype=np.int64)

    return AutomatonTransitionEncoding(
        from_state=trans.from_state,
        to_state=trans.to_state,
        variables=variables,
        P=P,
        r=r,
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
