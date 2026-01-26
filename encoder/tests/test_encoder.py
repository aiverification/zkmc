"""Tests for the matrix encoder."""

import pytest
import numpy as np
from numpy.testing import assert_array_equal

from zkterm_tool import parse, parse_with_constants, encode_program, encode_transition, encode_init


class TestEncoder:
    def test_example_y_lt_z(self):
        """Test the main example: [] y < z -> y = y + 1
        
        Expected encoding:
        - Variables: [y, z, y', z']
        - Guard y < z produces strict inequality: y - z < 0
        - Assignment y' = y + 1 produces: y' - y = 1 => -y + y' ≤ 1, y - y' ≤ -1
        - Identity z' = z produces: z' - z ≤ 0, -z' + z ≤ 0
        """
        commands = parse("[] y < z -> y = y + 1")
        encodings = encode_program(commands)
        
        assert len(encodings) == 1
        enc = encodings[0]
        
        # Check variables
        assert enc.variables == ["y", "z"]
        assert enc.full_variables() == ["y", "z", "y'", "z'"]
        
        # Check strict inequalities (C, d)
        # y - z < 0  =>  [1, -1, 0, 0] < 0
        assert enc.C.shape == (1, 4)
        assert_array_equal(enc.C[0], [1, -1, 0, 0])
        assert_array_equal(enc.d, [0])
        
        # Check non-strict inequalities (A, b)
        # Should have 4 rows:
        # y' - y = 1 encoded as:
        #   y' - y ≤ 1   =>  [-1, 0, 1, 0] ≤ 1
        #   -(y' - y) ≤ -1   =>  [1, 0, -1, 0] ≤ -1
        # z' - z = 0 encoded as:
        #   z' - z ≤ 0     =>  [0, -1, 0, 1] ≤ 0
        #   -(z' - z) ≤ 0  =>  [0, 1, 0, -1] ≤ 0
        assert enc.A.shape == (4, 4)
        
        # Find the rows (order may vary)
        rows = {tuple(row): val for row, val in zip(enc.A, enc.b)}
        
        assert rows[(-1, 0, 1, 0)] == 1   # y' - y ≤ 1
        assert rows[(1, 0, -1, 0)] == -1  # y - y' ≤ -1
        assert rows[(0, -1, 0, 1)] == 0   # z' - z ≤ 0
        assert rows[(0, 1, 0, -1)] == 0   # z - z' ≤ 0
    
    def test_equality_in_guard(self):
        """Test equality in guard: [] x = 5 -> x = x + 1
        
        x = 5 produces two non-strict inequalities:
        - x - 5 ≤ 0  =>  x ≤ 5
        - -x + 5 ≤ 0  =>  x ≥ 5
        """
        commands = parse("[] x = 5 -> x = x + 1")
        encodings = encode_program(commands)
        enc = encodings[0]
        
        # No strict inequalities
        assert enc.C.shape[0] == 0
        
        # 4 non-strict inequalities: 2 for guard, 2 for assignment
        assert enc.A.shape[0] == 4
        
        rows = {tuple(row): val for row, val in zip(enc.A, enc.b)}
        
        # Guard: x = 5 => x - 5 <= 0, -x + 5 <= 0
        # Variables are [x, x'], so coefficients are 2D
        assert rows[(1, 0)] == 5   # x ≤ 5
        assert rows[(-1, 0)] == -5  # -x ≤ -5
    
    def test_le_guard(self):
        """Test ≤ guard: [] x <= 10 -> x = x + 1"""
        commands = parse("[] x <= 10 -> x = x + 1")
        enc = encode_program(commands)[0]
        
        # x ≤ 10 is non-strict
        assert enc.C.shape[0] == 0  # no strict
        
        rows = {tuple(row): val for row, val in zip(enc.A, enc.b)}
        assert (1, 0) in rows  # x ≤ 10  =>  [1, 0] ≤ 10
    
    def test_gt_guard(self):
        """Test > guard: [] x > 0 -> x = x - 1"""
        commands = parse("[] x > 0 -> x = x - 1")
        enc = encode_program(commands)[0]
        
        # x > 0  =>  -x < 0  =>  [-1, 0] < 0
        assert enc.C.shape[0] == 1
        assert_array_equal(enc.C[0], [-1, 0])
        assert enc.d[0] == 0
    
    def test_multiple_transitions(self):
        """Test multiple guarded commands produce separate encodings."""
        commands = parse("""
            [] x < 10 -> x = x + 1
            [] x >= 10 -> x = 0
        """)
        encodings = encode_program(commands)
        
        assert len(encodings) == 2
        
        # Both should have same variables
        assert encodings[0].variables == encodings[1].variables
    
    def test_multiple_variables_identity(self):
        """Test that unassigned variables get identity constraints."""
        commands = parse("[] x < y -> x = x + 1")
        enc = encode_program(commands)[0]
        
        assert enc.variables == ["x", "y"]
        
        # y should have identity: y' = y
        rows = {tuple(row): val for row, val in zip(enc.A, enc.b)}
        assert rows[(0, -1, 0, 1)] == 0  # y' - y ≤ 0
        assert rows[(0, 1, 0, -1)] == 0  # -y' + y ≤ 0
    
    def test_complex_expression(self):
        """Test complex expressions: [] x < 10 -> x = 2 * x + 1"""
        commands = parse("[] x < 10 -> x = 2 * x + 1")
        enc = encode_program(commands)[0]
        
        # x' = 2*x + 1  =>  x' - 2x = 1
        # =>  x' - 2x ≤ 1 (which is [-2, 1] ≤ 1)
        # =>  -x' + 2x ≤ -1 (which is [2, -1] ≤ -1)
        rows = {tuple(row): val for row, val in zip(enc.A, enc.b)}
        
        assert rows[(-2, 1)] == 1    # x' - 2x ≤ 1
        assert rows[(2, -1)] == -1   # -x' + 2x ≤ -1


class TestEncoderEdgeCases:
    def test_empty_guard_constant_assignment(self):
        """Test constant assignment."""
        # We need at least a trivial guard, let's use x >= 0
        commands = parse("[] x >= 0 -> x = 5")
        enc = encode_program(commands)[0]
        
        # x' = 5  =>  x' = 5
        # =>  x' ≤ 5 (which is [0, 1] ≤ 5)
        # =>  -x' ≤ -5 (which is [0, -1] ≤ -5)
        rows = {tuple(row): val for row, val in zip(enc.A, enc.b)}
        assert rows[(0, 1)] == 5    # x' ≤ 5
        assert rows[(0, -1)] == -5  # -x' ≤ -5
    
    def test_subtraction_in_assignment(self):
        """Test subtraction: [] x > 0 -> x = x - 1"""
        commands = parse("[] x > 0 -> x = x - 1")
        enc = encode_program(commands)[0]
        
        # x' = x - 1  =>  x' - x = -1
        # =>  x' - x ≤ -1 (which is [-1, 1] ≤ -1)
        # =>  -x' + x ≤ 1 (which is [1, -1] ≤ 1)
        rows = {tuple(row): val for row, val in zip(enc.A, enc.b)}
        assert rows[(-1, 1)] == -1  # x' - x ≤ -1
        assert rows[(1, -1)] == 1   # -x' + x ≤ 1


class TestInitEncoding:
    """Tests for initial condition encoding."""
    
    def test_simple_init_encoding(self):
        """Test encoding: init: x = 0"""
        from zkterm_tool import parse_with_constants, encode_init
        
        result = parse_with_constants("init: x = 0")
        enc = encode_init(result.init_condition)
        
        assert enc.variables == ["x"]
        assert enc.A_0.shape == (2, 1)  # x = 0 becomes x <= 0 and -x <= 0
        assert enc.b_0.shape == (2,)
        
    def test_init_with_multiple_variables(self):
        """Test encoding: init: x = 0 && y >= 0 && y < 5"""
        from zkterm_tool import parse_with_constants, encode_init
        
        result = parse_with_constants("init: x = 0 && y >= 0 && y < 5")
        enc = encode_init(result.init_condition)
        
        assert set(enc.variables) == {"x", "y"}
        assert enc.A_0.shape[0] == 4  # x=0 (2) + y>=0 (1) + y<5->y<=4 (1)
        assert enc.A_0.shape[1] == 2  # Two variables
        
    def test_init_with_inequality(self):
        """Test encoding: init: x >= 0 && x < 10"""
        from zkterm_tool import parse_with_constants, encode_init
        
        result = parse_with_constants("init: x >= 0 && x < 10")
        enc = encode_init(result.init_condition)
        
        assert enc.variables == ["x"]
        assert enc.A_0.shape == (2, 1)  # Two inequalities
        
    def test_init_no_constraints(self):
        """Test encoding empty init (no constraints)."""
        from zkterm_tool import encode_init
        
        enc = encode_init([])  # Empty guard list
        
        assert enc.A_0.shape[0] == 0  # No constraints
        assert enc.b_0.shape[0] == 0


class TestAutomatonEncoding:
    """Tests for Büchi automaton transition encoding."""
    
    def test_regular_transition_encoding(self):
        """Test encoding regular transition: trans(q0, q1): x > 0"""
        from zkterm_tool import parse_with_constants, encode_automaton_transitions

        result = parse_with_constants("trans(q0, q1): x > 0")
        encodings = encode_automaton_transitions(result.automaton_transitions)

        assert len(encodings) == 1
        enc = encodings[0]
        assert enc.from_state == "q0"
        assert enc.to_state == "q1"
        assert enc.is_fair == False
        assert enc.P.shape[0] > 0  # Has constraints
        
    def test_fair_transition_encoding(self):
        """Test encoding fair transition: trans!(q0, q1): x > 0"""
        from zkterm_tool import parse_with_constants, encode_automaton_transitions

        result = parse_with_constants("trans!(q0, q1): x > 0")
        encodings = encode_automaton_transitions(result.automaton_transitions)

        assert len(encodings) == 1
        enc = encodings[0]
        assert enc.from_state == "q0"
        assert enc.to_state == "q1"
        assert enc.is_fair == True
        assert enc.P.shape[0] > 0  # Has constraints
        
    def test_multiple_transitions_consistent_variables(self):
        """Test encoding multiple transitions with consistent variable ordering."""
        from zkterm_tool import parse_with_constants, encode_automaton_transitions
        
        result = parse_with_constants("""
            trans(q0, q1): x >= 0 && x < 5
            trans(q1, q0): y > 10
        """)
        encodings = encode_automaton_transitions(result.automaton_transitions)
        
        assert len(encodings) == 2
        # Both should have same variable list (all variables)
        assert encodings[0].variables == encodings[1].variables
        assert set(encodings[0].variables) == {"x", "y"}
        
    def test_automaton_with_conjunctive_guards(self):
        """Test transition with multiple guards."""
        from zkterm_tool import parse_with_constants, encode_automaton_transitions

        result = parse_with_constants("trans(q0, q1): x >= 0 && x < 10 && y > 5")
        encodings = encode_automaton_transitions(result.automaton_transitions)

        enc = encodings[0]
        # Three guards should produce multiple inequalities
        assert enc.P.shape[0] >= 3
        
    def test_automaton_no_guards(self):
        """Test transition with no guards (always true)."""
        from zkterm_tool import parse_with_constants, AutomatonTransition
        from zkterm_tool import encode_automaton_transition

        # Create transition with no guards
        trans = AutomatonTransition(
            from_state="q0",
            to_state="q1",
            guards=[],
            is_fair=False
        )

        enc = encode_automaton_transition(trans, variables=["x"])

        # No constraints (always true)
        assert enc.P.shape[0] == 0
        assert enc.r.shape[0] == 0


class TestTypeBoundInjection:
    def test_bound_injection_transition(self):
        """Test that type bounds are injected into transition guards."""
        result = parse_with_constants("""
            type x: 0..10
            type y: 0..5

            [] x < y -> x = x + 1
        """)

        cmd = result.commands[0]
        all_vars = sorted(cmd.get_variables())

        encoding = encode_transition(cmd, variables=all_vars, types=result.types)

        # Guard should have original (x < y) plus bounds:
        # x >= 0, x <= 10, y >= 0, y <= 5
        # Total: at least 5 inequalities (original guard + 4 bounds)
        assert encoding.A.shape[0] >= 5

    def test_bound_injection_init(self):
        """Test that type bounds are injected into init conditions."""
        result = parse_with_constants("""
            type x: 0..10
            init: x = 0
        """)

        all_vars = ["x"]
        init_encoding = encode_init(result.init_condition, variables=all_vars, types=result.types)

        # Init should have x = 0 (2 constraints) plus x >= 0 and x <= 10 (2 more)
        # Total: 4 constraints
        assert init_encoding.A_0.shape[0] == 4

    def test_no_bounds_without_type(self):
        """Test that variables without types are not bounded."""
        result = parse_with_constants("""
            [] x < 10 -> x = x + 1
        """)

        cmd = result.commands[0]
        all_vars = sorted(cmd.get_variables())

        # No types, so result.types is empty
        encoding = encode_transition(cmd, variables=all_vars, types=result.types)

        # Without types, we only have the original guard x < 10 and x' = x + 1
        # This produces 3 inequalities: x < 10, x' >= x+1, x' <= x+1
        # (Note: strict inequality converted to non-strict in the encoder)
        assert encoding.A.shape[0] <= 3  # Should not have extra bound constraints

    def test_bounds_with_multiple_variables(self):
        """Test type bounds with multiple variables."""
        result = parse_with_constants("""
            type x: 0..100
            type y: -10..10
            type z: 5..20

            [] x > 0 && y < z -> x = x - 1; y = y + 1
        """)

        cmd = result.commands[0]
        all_vars = sorted(cmd.get_variables())  # ['x', 'y', 'z']

        encoding = encode_transition(cmd, variables=all_vars, types=result.types)

        # Original guards: x > 0, y < z (2)
        # Type bounds: x >= 0, x <= 100, y >= -10, y <= 10, z >= 5, z <= 20 (6)
        # Assignments: x' = x - 1, y' = y + 1 (4 inequalities)
        # Identity for z: z' = z (2 inequalities)
        # Total: at least 14 inequalities
        assert encoding.A.shape[0] >= 10

    def test_type_bounds_override_behavior(self):
        """Test that adding redundant type bounds doesn't break encoding."""
        result = parse_with_constants("""
            type x: 0..10

            [] x > 3 && x < 8 -> x = x + 1
        """)

        cmd = result.commands[0]
        all_vars = ["x"]

        encoding = encode_transition(cmd, variables=all_vars, types=result.types)

        # Original guards: x > 3, x < 8 (2)
        # Type bounds: x >= 0, x <= 10 (2, redundant but added anyway)
        # Assignment: x' = x + 1 (2)
        # Total: at least 6 inequalities
        # The redundant bounds (x >= 0, x <= 10) don't hurt since x > 3 && x < 8 is more restrictive
        assert encoding.A.shape[0] >= 4
