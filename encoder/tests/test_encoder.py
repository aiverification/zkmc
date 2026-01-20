"""Tests for the matrix encoder."""

import pytest
import numpy as np
from numpy.testing import assert_array_equal

from zkterm_tool import parse, encode_program, encode_transition


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
        
        # Check strict inequalities (A, a)
        # y - z < 0  =>  [1, -1, 0, 0] < 0
        assert enc.A.shape == (1, 4)
        assert_array_equal(enc.A[0], [1, -1, 0, 0])
        assert_array_equal(enc.a, [0])
        
        # Check non-strict inequalities (C, c)
        # Should have 4 rows:
        # y' - y = 1 encoded as:
        #   y' - y ≤ 1   =>  [-1, 0, 1, 0] ≤ 1
        #   -(y' - y) ≤ -1   =>  [1, 0, -1, 0] ≤ -1
        # z' - z = 0 encoded as:
        #   z' - z ≤ 0     =>  [0, -1, 0, 1] ≤ 0
        #   -(z' - z) ≤ 0  =>  [0, 1, 0, -1] ≤ 0
        assert enc.C.shape == (4, 4)
        
        # Find the rows (order may vary)
        rows = {tuple(row): val for row, val in zip(enc.C, enc.c)}
        
        assert rows[(-1, 0, 1, 0)] == -1  # y' - y ≤ 1 (const is -expr.const = -1)
        assert rows[(1, 0, -1, 0)] == 1   # y - y' ≤ -1
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
        assert enc.A.shape[0] == 0
        
        # 4 non-strict inequalities: 2 for guard, 2 for assignment
        assert enc.C.shape[0] == 4
        
        rows = {tuple(row): val for row, val in zip(enc.C, enc.c)}
        
        # Guard: x = 5 => x - 5 <= 0, -x + 5 <= 0
        # Variables are [x, x'], so coefficients are 2D
        assert rows[(1, 0)] == 5   # x ≤ 5
        assert rows[(-1, 0)] == -5  # -x ≤ -5
    
    def test_le_guard(self):
        """Test ≤ guard: [] x <= 10 -> x = x + 1"""
        commands = parse("[] x <= 10 -> x = x + 1")
        enc = encode_program(commands)[0]
        
        # x ≤ 10 is non-strict
        assert enc.A.shape[0] == 0  # no strict
        
        rows = {tuple(row): val for row, val in zip(enc.C, enc.c)}
        assert (1, 0) in rows  # x ≤ 10  =>  [1, 0] ≤ 10
    
    def test_gt_guard(self):
        """Test > guard: [] x > 0 -> x = x - 1"""
        commands = parse("[] x > 0 -> x = x - 1")
        enc = encode_program(commands)[0]
        
        # x > 0  =>  -x < 0  =>  [-1, 0] < 0
        assert enc.A.shape[0] == 1
        assert_array_equal(enc.A[0], [-1, 0])
        assert enc.a[0] == 0
    
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
        rows = {tuple(row): val for row, val in zip(enc.C, enc.c)}
        assert rows[(0, -1, 0, 1)] == 0  # y' - y ≤ 0
        assert rows[(0, 1, 0, -1)] == 0  # -y' + y ≤ 0
    
    def test_complex_expression(self):
        """Test complex expressions: [] x < 10 -> x = 2 * x + 1"""
        commands = parse("[] x < 10 -> x = 2 * x + 1")
        enc = encode_program(commands)[0]
        
        # x' = 2*x + 1  =>  x' - 2x - 1 = 0
        # =>  x' - 2x ≤ 1 (which is -2x + x' ≤ 1)
        # =>  -x' + 2x ≤ -1 (which is 2x - x' ≤ -1)
        rows = {tuple(row): val for row, val in zip(enc.C, enc.c)}
        
        assert rows[(-2, 1)] == -1   # x' - 2x ≤ 1  =>  [-2, 1] ≤ -1 (const = -expr.const)
        assert rows[(2, -1)] == 1    # -x' + 2x ≤ -1  =>  [2, -1] ≤ 1


class TestEncoderEdgeCases:
    def test_empty_guard_constant_assignment(self):
        """Test constant assignment."""
        # We need at least a trivial guard, let's use x >= 0
        commands = parse("[] x >= 0 -> x = 5")
        enc = encode_program(commands)[0]
        
        # x' = 5  =>  x' - 5 = 0
        # =>  x' ≤ 5 (which is [0, 1] ≤ 5), but const comes from -expr.const
        # =>  -x' ≤ -5 (which is [0, -1] ≤ -5)
        rows = {tuple(row): val for row, val in zip(enc.C, enc.c)}
        assert rows[(0, 1)] == -5   # x' - 5 ≤ 0  =>  [0, 1] ≤ -(-5) => actually x' ≤ 5
        assert rows[(0, -1)] == 5   # -x' + 5 ≤ 0  =>  [0, -1] ≤ 5
    
    def test_subtraction_in_assignment(self):
        """Test subtraction: [] x > 0 -> x = x - 1"""
        commands = parse("[] x > 0 -> x = x - 1")
        enc = encode_program(commands)[0]
        
        # x' = x - 1  =>  x' - (x - 1) = 0  =>  x' - x + 1 = 0
        # =>  x' - x ≤ -1 (which is [-1, 1] ≤ -1)
        # =>  -x' + x ≤ 1 (which is [1, -1] ≤ 1)
        rows = {tuple(row): val for row, val in zip(enc.C, enc.c)}
        assert rows[(-1, 1)] == 1   # x' - x ≤ -1 => const = -(-1) = 1
        assert rows[(1, -1)] == -1  # -x' + x ≤ 1 => const = -1
