"""Tests for the guarded command parser."""

import pytest
from zkterm_tool import parse, GuardedCommand, Comparison, Assignment, CompOp, Var, Num, BinOp


class TestParser:
    def test_simple_guard_and_assignment(self):
        """Test parsing: [] y < z -> y = y + 1"""
        result = parse("[] y < z -> y = y + 1")
        
        assert len(result) == 1
        cmd = result[0]
        
        # Check guard
        assert len(cmd.guards) == 1
        guard = cmd.guards[0]
        assert guard.op == CompOp.LT
        assert guard.left == Var("y")
        assert guard.right == Var("z")
        
        # Check assignment
        assert len(cmd.assignments) == 1
        assign = cmd.assignments[0]
        assert assign.var == "y"
        assert assign.expr == BinOp("+", Var("y"), Num(1))
    
    def test_multiple_assignments(self):
        """Test parsing multiple assignments."""
        result = parse("[] x <= 10 -> x = x + 1; y = y - 1")
        
        assert len(result) == 1
        cmd = result[0]
        assert len(cmd.assignments) == 2
        assert cmd.assignments[0].var == "x"
        assert cmd.assignments[1].var == "y"
    
    def test_multiple_guards(self):
        """Test parsing conjunctive guards."""
        result = parse("[] x > 0 && x < 10 -> x = x + 1")
        
        assert len(result) == 1
        cmd = result[0]
        assert len(cmd.guards) == 2
        assert cmd.guards[0].op == CompOp.GT
        assert cmd.guards[1].op == CompOp.LT
    
    def test_multiple_commands(self):
        """Test parsing multiple guarded commands."""
        result = parse("""
            [] x < 10 -> x = x + 1
            [] x >= 10 -> x = 0
        """)
        
        assert len(result) == 2
    
    def test_equality_guard(self):
        """Test parsing equality in guard."""
        result = parse("[] x = 5 -> x = x + 1")
        
        cmd = result[0]
        assert cmd.guards[0].op == CompOp.EQ
    
    def test_negative_numbers(self):
        """Test parsing negative numbers."""
        result = parse("[] x > -5 -> x = x - 1")
        
        cmd = result[0]
        # -5 is parsed as Neg(Num(5))
        assert cmd.guards[0].right.expr.value == 5
    
    def test_multiplication(self):
        """Test parsing multiplication."""
        result = parse("[] x < 10 -> x = 2 * x")
        
        cmd = result[0]
        assert cmd.assignments[0].expr == BinOp("*", Num(2), Var("x"))
    
    def test_unicode_operators(self):
        """Test parsing Unicode operators ≤ and ≥."""
        result = parse("[] x ≤ 10 && y ≥ 0 -> x = x + 1")
        
        cmd = result[0]
        assert cmd.guards[0].op == CompOp.LE
        assert cmd.guards[1].op == CompOp.GE
