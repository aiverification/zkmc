"""Tests for state enumeration functionality."""

import pytest
import numpy as np
from zkterm_tool.state_enumerator import (
    StateBounds, StateSpace, parse_bounds_arg, create_state_space
)


def test_enumerate_small_space():
    """Test enumeration of 2D state space."""
    bounds = {
        "x": StateBounds("x", 0, 2),
        "y": StateBounds("y", 0, 1)
    }
    space = StateSpace(["x", "y"], bounds)

    states = list(space.enumerate_states())

    # Should have 3 * 2 = 6 states
    assert len(states) == 6
    assert {"x": 0, "y": 0} in states
    assert {"x": 0, "y": 1} in states
    assert {"x": 1, "y": 0} in states
    assert {"x": 1, "y": 1} in states
    assert {"x": 2, "y": 0} in states
    assert {"x": 2, "y": 1} in states


def test_enumerate_single_variable():
    """Test enumeration with single variable."""
    bounds = {"x": StateBounds("x", 0, 3)}
    space = StateSpace(["x"], bounds)

    states = list(space.enumerate_states())

    assert len(states) == 4
    assert {"x": 0} in states
    assert {"x": 1} in states
    assert {"x": 2} in states
    assert {"x": 3} in states


def test_state_to_vector():
    """Test converting state dict to vector."""
    bounds = {
        "x": StateBounds("x", 0, 10),
        "y": StateBounds("y", 0, 10)
    }
    space = StateSpace(["x", "y"], bounds)

    state = {"x": 5, "y": 3}
    vec = space.state_to_vector(state)

    assert isinstance(vec, np.ndarray)
    assert vec.dtype == np.int64
    assert len(vec) == 2
    assert vec[0] == 5
    assert vec[1] == 3


def test_vector_to_state():
    """Test converting vector to state dict."""
    bounds = {
        "x": StateBounds("x", 0, 10),
        "y": StateBounds("y", 0, 10)
    }
    space = StateSpace(["x", "y"], bounds)

    vec = np.array([5, 3])
    state = space.vector_to_state(vec)

    assert state == {"x": 5, "y": 3}


def test_parse_bounds_valid():
    """Test parsing valid bounds."""
    bound = parse_bounds_arg("x:0:10")
    assert bound.variable == "x"
    assert bound.min_value == 0
    assert bound.max_value == 10


def test_parse_bounds_negative():
    """Test parsing bounds with negative values."""
    bound = parse_bounds_arg("y:-5:5")
    assert bound.variable == "y"
    assert bound.min_value == -5
    assert bound.max_value == 5


def test_parse_bounds_invalid_format():
    """Test parsing with invalid format."""
    with pytest.raises(ValueError, match="Invalid bound format"):
        parse_bounds_arg("invalid")

    with pytest.raises(ValueError, match="Invalid bound format"):
        parse_bounds_arg("x:0")  # Missing max


def test_parse_bounds_invalid_integer():
    """Test parsing with non-integer values."""
    with pytest.raises(ValueError, match="Invalid integer"):
        parse_bounds_arg("x:abc:10")

    with pytest.raises(ValueError, match="Invalid integer"):
        parse_bounds_arg("x:0:xyz")


def test_parse_bounds_min_greater_than_max():
    """Test parsing with min > max."""
    with pytest.raises(ValueError, match="Min value .* > max value"):
        parse_bounds_arg("x:10:0")


def test_create_state_space_valid():
    """Test creating state space with valid bounds."""
    variables = ["x", "y"]
    bounds_args = ["x:0:10", "y:0:5"]

    space = create_state_space(variables, bounds_args)

    assert space.variables == ["x", "y"]
    assert "x" in space.bounds
    assert "y" in space.bounds
    assert space.bounds["x"].min_value == 0
    assert space.bounds["x"].max_value == 10
    assert space.bounds["y"].min_value == 0
    assert space.bounds["y"].max_value == 5


def test_create_state_space_missing_bounds():
    """Test creating state space with missing bounds."""
    variables = ["x", "y", "z"]
    bounds_args = ["x:0:10", "y:0:5"]  # Missing z

    with pytest.raises(ValueError, match="Missing bounds for variables: \\['z'\\]"):
        create_state_space(variables, bounds_args)


def test_create_state_space_extra_bounds():
    """Test creating state space with extra bounds."""
    variables = ["x", "y"]
    bounds_args = ["x:0:10", "y:0:5", "z:0:3"]  # Extra z

    with pytest.raises(ValueError, match="Bounds specified for unknown variables: \\['z'\\]"):
        create_state_space(variables, bounds_args)


def test_enumerate_order():
    """Test that enumeration follows correct order."""
    bounds = {
        "x": StateBounds("x", 0, 1),
        "y": StateBounds("y", 0, 1)
    }
    space = StateSpace(["x", "y"], bounds)

    states = list(space.enumerate_states())

    # itertools.product should give: (0,0), (0,1), (1,0), (1,1)
    assert states[0] == {"x": 0, "y": 0}
    assert states[1] == {"x": 0, "y": 1}
    assert states[2] == {"x": 1, "y": 0}
    assert states[3] == {"x": 1, "y": 1}
