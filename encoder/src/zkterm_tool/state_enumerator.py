"""State space enumeration for explicit-state verification.

This module provides functionality to enumerate finite state spaces for
explicit-state verification. It supports defining bounds on variables and
generating all possible states within those bounds.
"""

from typing import Dict, List, Iterator
import numpy as np
from dataclasses import dataclass
import itertools


@dataclass
class StateBounds:
    """Bounds for a single variable.

    Attributes:
        variable: Variable name
        min_value: Minimum value (inclusive)
        max_value: Maximum value (inclusive)
    """
    variable: str
    min_value: int
    max_value: int


@dataclass
class StateSpace:
    """Represents the enumerable state space.

    Attributes:
        variables: Ordered list of variable names
        bounds: Dictionary mapping variable names to their bounds
    """
    variables: List[str]
    bounds: Dict[str, StateBounds]

    def enumerate_states(self) -> Iterator[Dict[str, int]]:
        """Generate all states within bounds as dictionaries.

        Uses itertools.product to efficiently generate the Cartesian product
        of all variable ranges.

        Yields:
            State dictionaries like {'x': 0, 'y': 5, ...}

        Example:
            >>> space = StateSpace(['x', 'y'],
            ...                    {'x': StateBounds('x', 0, 2),
            ...                     'y': StateBounds('y', 0, 1)})
            >>> list(space.enumerate_states())
            [{'x': 0, 'y': 0}, {'x': 0, 'y': 1}, {'x': 1, 'y': 0}, ...]
        """
        # Build ranges for each variable in order
        ranges = [
            range(self.bounds[var].min_value,
                  self.bounds[var].max_value + 1)
            for var in self.variables
        ]

        # Generate all combinations using Cartesian product
        for values in itertools.product(*ranges):
            yield dict(zip(self.variables, values))

    def state_to_vector(self, state: Dict[str, int]) -> np.ndarray:
        """Convert state dict to ordered vector.

        Args:
            state: State as dictionary {'var': value, ...}

        Returns:
            NumPy array with values in variable order

        Example:
            >>> space = StateSpace(['x', 'y'], {...})
            >>> space.state_to_vector({'x': 5, 'y': 3})
            array([5, 3])
        """
        return np.array([state[var] for var in self.variables],
                       dtype=np.int64)

    def vector_to_state(self, vector: np.ndarray) -> Dict[str, int]:
        """Convert ordered vector to state dict.

        Args:
            vector: NumPy array with values in variable order

        Returns:
            State dictionary {'var': value, ...}

        Example:
            >>> space = StateSpace(['x', 'y'], {...})
            >>> space.vector_to_state(np.array([5, 3]))
            {'x': 5, 'y': 3}
        """
        return dict(zip(self.variables, vector))


def parse_bounds_arg(bounds_str: str) -> StateBounds:
    """Parse a single bound specification.

    Args:
        bounds_str: Format "var:min:max" e.g., "x:0:10"

    Returns:
        StateBounds object

    Raises:
        ValueError: If format is invalid, integers cannot be parsed,
                   or min_value > max_value

    Example:
        >>> parse_bounds_arg("x:0:10")
        StateBounds(variable='x', min_value=0, max_value=10)
    """
    parts = bounds_str.split(':')
    if len(parts) != 3:
        raise ValueError(
            f"Invalid bound format '{bounds_str}'. "
            f"Expected 'var:min:max' (e.g., 'x:0:10')"
        )

    var, min_str, max_str = parts

    try:
        min_val = int(min_str)
        max_val = int(max_str)
    except ValueError:
        raise ValueError(
            f"Invalid integer in bound '{bounds_str}'"
        )

    if min_val > max_val:
        raise ValueError(
            f"Min value {min_val} > max value {max_val} "
            f"for variable '{var}'"
        )

    return StateBounds(var, min_val, max_val)


def create_state_space(
    variables: List[str],
    bounds_args: List[str]
) -> StateSpace:
    """Create StateSpace from command-line bounds.

    Args:
        variables: All program variables (sorted)
        bounds_args: List of "var:min:max" strings

    Returns:
        StateSpace object

    Raises:
        ValueError: If bounds are invalid, missing for some variables,
                   or specified for unknown variables

    Example:
        >>> create_state_space(['x', 'y'], ['x:0:10', 'y:0:5'])
        StateSpace(variables=['x', 'y'], bounds={...})
    """
    # Parse all bounds
    bounds_list = [parse_bounds_arg(b) for b in bounds_args]
    bounds_dict = {b.variable: b for b in bounds_list}

    # Check all variables have bounds
    missing = set(variables) - set(bounds_dict.keys())
    if missing:
        raise ValueError(
            f"Missing bounds for variables: {sorted(missing)}"
        )

    # Check no extra bounds
    extra = set(bounds_dict.keys()) - set(variables)
    if extra:
        raise ValueError(
            f"Bounds specified for unknown variables: {sorted(extra)}"
        )

    return StateSpace(variables, bounds_dict)
