"""Tests for the guarded command and ranking function parser."""

import pytest
from zkterm_tool import (
    parse, parse_with_constants, GuardedCommand, Comparison, Assignment, CompOp,
    Var, Num, BinOp, RankingCase, RankingFunction, AutomatonTransition,
    encode_ranking_function
)


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


class TestConstants:
    def test_constant_substitution(self):
        """Test that constants are substituted in expressions."""
        result = parse("""
            const received = 1
            const wait = 0
            [] ack = received && status = wait -> status = received
        """)
        
        cmd = result[0]
        # Constants should be substituted with their values
        assert cmd.guards[0].right == Num(1)  # received -> 1
        assert cmd.guards[1].right == Num(0)  # wait -> 0
        assert cmd.assignments[0].expr == Num(1)  # received -> 1
    
    def test_constants_in_expressions(self):
        """Test constants in arithmetic expressions."""
        result = parse("""
            const inc = 2
            [] x < 10 -> x = x + inc
        """)
        
        cmd = result[0]
        assert cmd.assignments[0].expr == BinOp("+", Var("x"), Num(2))
    
    def test_parse_with_constants_returns_constants(self):
        """Test that parse_with_constants returns the constant definitions."""
        result = parse_with_constants("""
            const a = 1
            const b = 2
            [] x = a -> x = b
        """)
        
        assert result.constants == {"a": 1, "b": 2}
        assert len(result.commands) == 1
    
    def test_comments(self):
        """Test that comments are ignored."""
        result = parse("""
            // This is a comment
            const x = 1  // inline comment
            [] y = x -> y = 0  // another comment
        """)

        assert len(result) == 1
        assert result[0].guards[0].right == Num(1)


class TestRankingFunctions:
    def test_simple_ranking_function(self):
        """Test parsing: rank(q0): [] x > 0 -> x"""
        result = parse_with_constants("""
            rank(q0):
                [] x > 0 -> x
        """)

        assert len(result.ranking_functions) == 1
        assert "q0" in result.ranking_functions

        rf = result.ranking_functions["q0"]
        assert rf.state == "q0"
        assert len(rf.cases) == 1

        case = rf.cases[0]
        assert len(case.guards) == 1
        assert case.guards[0].op == CompOp.GT
        assert case.guards[0].left == Var("x")
        assert case.guards[0].right == Num(0)
        assert case.expression == Var("x")

    def test_multiple_cases(self):
        """Test parsing ranking function with multiple cases."""
        result = parse_with_constants("""
            rank(q0):
                [] x >= 0 && x < 10 -> 10 - x
                [] x >= 10 -> 1
        """)

        rf = result.ranking_functions["q0"]
        assert len(rf.cases) == 2

        # First case
        case1 = rf.cases[0]
        assert len(case1.guards) == 2
        assert case1.guards[0].op == CompOp.GE
        assert case1.guards[1].op == CompOp.LT
        assert case1.expression == BinOp("-", Num(10), Var("x"))

        # Second case
        case2 = rf.cases[1]
        assert len(case2.guards) == 1
        assert case2.guards[0].op == CompOp.GE
        assert case2.expression == Num(1)

    def test_multiple_ranking_functions(self):
        """Test parsing multiple ranking functions for different states."""
        result = parse_with_constants("""
            rank(q0):
                [] x > 0 -> x

            rank(q1):
                [] y > 0 -> 2 * y
        """)

        assert len(result.ranking_functions) == 2
        assert "q0" in result.ranking_functions
        assert "q1" in result.ranking_functions

        rf0 = result.ranking_functions["q0"]
        assert rf0.state == "q0"
        assert rf0.cases[0].expression == Var("x")

        rf1 = result.ranking_functions["q1"]
        assert rf1.state == "q1"
        assert rf1.cases[0].expression == BinOp("*", Num(2), Var("y"))

    def test_ranking_with_constants(self):
        """Test ranking functions with constant substitution."""
        result = parse_with_constants("""
            const maxVal = 10

            rank(q0):
                [] x < maxVal -> maxVal - x
        """)

        rf = result.ranking_functions["q0"]
        case = rf.cases[0]

        # Constants should be substituted
        assert case.guards[0].right == Num(10)  # maxVal -> 10
        assert case.expression == BinOp("-", Num(10), Var("x"))

    def test_mixed_commands_and_ranking(self):
        """Test parsing file with both guarded commands and ranking functions."""
        result = parse_with_constants("""
            [] x < 10 -> x = x + 1
            [] x >= 10 -> x = 0

            rank(q0):
                [] x >= 0 && x < 10 -> 10 - x
                [] x >= 10 -> 1
        """)

        # Should have both commands and ranking functions
        assert len(result.commands) == 2
        assert len(result.ranking_functions) == 1

        # Check that both are parsed correctly
        assert result.commands[0].assignments[0].var == "x"
        assert result.ranking_functions["q0"].state == "q0"

    def test_complex_expression(self):
        """Test ranking with complex linear expression."""
        result = parse_with_constants("""
            rank(q0):
                [] x > 0 && y > 0 -> 2*x + 3*y - 1
        """)

        rf = result.ranking_functions["q0"]
        case = rf.cases[0]

        # Expression: 2*x + 3*y - 1
        # Parsed as: (2*x + 3*y) - 1
        assert isinstance(case.expression, BinOp)
        assert case.expression.op == "-"


class TestInitialConditions:
    """Tests for initial condition parsing."""
    
    def test_simple_init_condition(self):
        """Test parsing: init: x = 0"""
        result = parse_with_constants("init: x = 0")

        assert result.init_condition is not None
        assert len(result.init_condition) == 1  # One comparison: x = 0
        assert result.init_condition[0].op == CompOp.EQ
        
    def test_init_with_conjunctive_guards(self):
        """Test parsing: init: x >= 0 && x < 10"""
        result = parse_with_constants("init: x >= 0 && x < 10")
        
        assert result.init_condition is not None
        assert len(result.init_condition) == 2
        
    def test_init_with_multiple_variables(self):
        """Test parsing: init: x = 0 && y >= 0 && y < 5"""
        result = parse_with_constants("init: x = 0 && y >= 0 && y < 5")

        assert result.init_condition is not None
        assert len(result.init_condition) == 3  # Three comparisons: x=0, y>=0, y<5
        
    def test_init_with_constants(self):
        """Test init using constants."""
        result = parse_with_constants("""
            const maxVal = 10
            init: x = 0 && y < maxVal
        """)
        
        assert result.init_condition is not None
        assert result.constants["maxVal"] == 10
        
    def test_init_with_commands(self):
        """Test file with both init and commands."""
        result = parse_with_constants("""
            init: x = 0
            [] x < 10 -> x = x + 1
        """)
        
        assert result.init_condition is not None
        assert len(result.commands) == 1
        
    def test_no_init_condition(self):
        """Test file without init condition."""
        result = parse_with_constants("[] x < 10 -> x = x + 1")
        
        assert result.init_condition is None
        assert len(result.commands) == 1


class TestAutomatonTransitions:
    """Tests for Büchi automaton transition parsing."""
    
    def test_simple_regular_transition(self):
        """Test parsing: trans(q0, q1): x > 0"""
        result = parse_with_constants("trans(q0, q1): x > 0")
        
        assert len(result.automaton_transitions) == 1
        trans = result.automaton_transitions[0]
        assert trans.from_state == "q0"
        assert trans.to_state == "q1"
        assert trans.is_fair == False
        assert len(trans.guards) == 1
        
    def test_fair_transition(self):
        """Test parsing fair transition: trans!(q0, q1): x > 0"""
        result = parse_with_constants("trans!(q0, q1): x > 0")
        
        assert len(result.automaton_transitions) == 1
        trans = result.automaton_transitions[0]
        assert trans.from_state == "q0"
        assert trans.to_state == "q1"
        assert trans.is_fair == True
        
    def test_multiple_automaton_transitions(self):
        """Test parsing multiple transitions."""
        result = parse_with_constants("""
            trans(q0, q1): x >= 0 && x < 5
            trans!(q1, q1): x > 0
            trans(q1, q0): x >= 10
        """)
        
        assert len(result.automaton_transitions) == 3
        assert result.automaton_transitions[0].is_fair == False
        assert result.automaton_transitions[1].is_fair == True
        assert result.automaton_transitions[2].is_fair == False
        
    def test_automaton_with_conjunctive_guards(self):
        """Test transition with multiple guards."""
        result = parse_with_constants("trans(q0, q1): x >= 0 && x < 10 && y > 5")
        
        trans = result.automaton_transitions[0]
        assert len(trans.guards) == 3
        
    def test_mixed_program(self):
        """Test file with init, commands, ranking, and automaton."""
        result = parse_with_constants("""
            const maxVal = 10
            
            init: x = 0 && y = 0
            
            [] x < maxVal -> x = x + 1
            
            rank(q0):
                [] x >= 0 -> maxVal - x
            
            trans(q0, q1): x >= 0 && x < 5
            trans!(q1, q0): x >= 10
        """)
        
        # Check all components present
        assert result.constants == {"maxVal": 10}
        assert result.init_condition is not None
        assert len(result.commands) == 1
        assert len(result.ranking_functions) == 1
        assert len(result.automaton_transitions) == 2


class TestTrueKeyword:
    """Tests for the 'true' keyword in guards."""

    def test_true_in_ranking_function(self):
        """Test parsing: rank(q0): [] true -> 1"""
        result = parse_with_constants("""
            rank(q0):
                [] true -> 1
        """)

        assert len(result.ranking_functions) == 1
        rf = result.ranking_functions["q0"]
        assert rf.state == "q0"
        assert len(rf.cases) == 1
        assert len(rf.cases[0].guards) == 0  # Empty guard = true
        assert rf.cases[0].expression == Num(1)

    def test_true_in_init_condition(self):
        """Test parsing: init: true"""
        result = parse_with_constants("init: true")

        assert result.init_condition is not None
        assert len(result.init_condition) == 0  # Empty guard list

    def test_true_in_guarded_command(self):
        """Test parsing: [] true -> x = 1"""
        result = parse_with_constants("[] true -> x = 1")

        assert len(result.commands) == 1
        assert len(result.commands[0].guards) == 0  # Empty guard list
        assert result.commands[0].assignments[0].var == "x"
        assert result.commands[0].assignments[0].expr == Num(1)

    def test_true_in_automaton_transition(self):
        """Test parsing: trans(q0, q1): true"""
        result = parse_with_constants("trans(q0, q1): true")

        assert len(result.automaton_transitions) == 1
        trans = result.automaton_transitions[0]
        assert trans.from_state == "q0"
        assert trans.to_state == "q1"
        assert len(trans.guards) == 0  # Empty guard list
        assert trans.is_fair == False

    def test_true_equivalent_to_empty_guard(self):
        """Verify 'true' behaves identically to programmatically created empty guard."""
        # Parse with 'true' keyword
        result1 = parse_with_constants("rank(q0): [] true -> 1")

        # Create programmatically with empty guards
        result2_rf = RankingFunction(
            state="q0",
            cases=[RankingCase(guards=[], expression=Num(1), is_infinity=False)]
        )

        enc1 = encode_ranking_function(result1.ranking_functions["q0"])
        enc2 = encode_ranking_function(result2_rf)

        # Both should have empty guard matrices (0 rows)
        assert enc1.finite_cases[0].C_j.shape == enc2.finite_cases[0].C_j.shape
        assert enc1.finite_cases[0].C_j.shape[0] == 0  # 0 rows = no constraints
        assert enc1.finite_cases[0].d_j.shape == enc2.finite_cases[0].d_j.shape
        assert enc1.finite_cases[0].d_j.shape[0] == 0

    def test_fair_automaton_with_true(self):
        """Test fair automaton transition with true guard."""
        result = parse_with_constants("trans!(q0, q1): true")

        assert len(result.automaton_transitions) == 1
        trans = result.automaton_transitions[0]
        assert trans.is_fair == True
        assert len(trans.guards) == 0

    def test_automaton_init_single_state(self):
        """Test automaton_init with single state."""
        result = parse_with_constants("automaton_init: q0")

        assert result.automaton_initial_states is not None
        assert result.automaton_initial_states == ["q0"]

    def test_automaton_init_multiple_states(self):
        """Test automaton_init with multiple states."""
        result = parse_with_constants("automaton_init: q0, q1, q2")

        assert result.automaton_initial_states is not None
        assert result.automaton_initial_states == ["q0", "q1", "q2"]

    def test_automaton_init_not_specified(self):
        """Test that automaton_init is None when not specified."""
        result = parse_with_constants("[] x > 0 -> x = x + 1")

        assert result.automaton_initial_states is None

    def test_multiple_cases_with_true(self):
        """Test ranking function with multiple cases including true."""
        result = parse_with_constants("""
            rank(q0):
                [] x >= 0 && x < 10 -> 10 - x
                [] true -> 1
        """)

        rf = result.ranking_functions["q0"]
        assert len(rf.cases) == 2

        # First case has guards
        assert len(rf.cases[0].guards) == 2

        # Second case has no guards (true)
        assert len(rf.cases[1].guards) == 0
        assert rf.cases[1].expression == Num(1)

class TestInfinityCases:
    """Tests for parsing infinity cases with 'inf' keyword."""

    def test_simple_infinity_case(self):
        """Test parsing a simple infinity case."""
        result = parse_with_constants("""
            rank(q0):
                [] x < 0 -> inf
        """)

        rf = result.ranking_functions["q0"]
        assert len(rf.cases) == 1
        case = rf.cases[0]
        assert case.is_infinity is True
        assert case.expression is None
        assert len(case.guards) == 1

    def test_mixed_finite_and_infinity_cases(self):
        """Test parsing mixed finite and infinity cases."""
        result = parse_with_constants("""
            rank(q0):
                [] x >= 0 && x <= 10 -> 10 - x
                [] x < 0 -> inf
                [] x > 10 -> inf
        """)

        rf = result.ranking_functions["q0"]
        assert len(rf.cases) == 3

        # First case is finite
        assert rf.cases[0].is_infinity is False
        assert rf.cases[0].expression is not None

        # Second and third cases are infinity
        assert rf.cases[1].is_infinity is True
        assert rf.cases[1].expression is None
        assert rf.cases[2].is_infinity is True
        assert rf.cases[2].expression is None

    def test_infinity_case_encoding(self):
        """Test encoding infinity cases."""
        from zkterm_tool import encode_ranking_function

        result = parse_with_constants("""
            rank(q0):
                [] x >= 0 && x <= 10 -> 10 - x
                [] x < 0 -> inf
        """)

        enc = encode_ranking_function(result.ranking_functions["q0"])

        # Should have 1 finite case and 1 infinity case
        assert len(enc.finite_cases) == 1
        assert len(enc.infinity_cases) == 1

        # Check finite case
        finite = enc.finite_cases[0]
        assert finite.w_j is not None
        assert finite.u_j == 10

        # Check infinity case
        infinity = enc.infinity_cases[0]
        assert infinity.E_k is not None
        assert infinity.f_k is not None


def test_const_override():
    """Test that constant overrides work correctly."""
    program = """
        const maxVal = 10
        const threshold = 5

        init: x = 0

        [] x < maxVal -> x = x + 1

        rank(q0):
            [] x >= 0 && x <= maxVal -> maxVal - x
    """

    # Parse without override
    result1 = parse_with_constants(program)
    assert result1.constants == {"maxVal": 10, "threshold": 5}

    # Parse with single override
    result2 = parse_with_constants(program, const_overrides={"maxVal": 3})
    assert result2.constants == {"maxVal": 3, "threshold": 5}

    # Parse with multiple overrides
    result3 = parse_with_constants(program, const_overrides={"maxVal": 7, "threshold": 2})
    assert result3.constants == {"maxVal": 7, "threshold": 2}

    # Verify the override affects the AST
    # The transition guard should be x < maxVal
    cmd = result2.commands[0]
    # Find the x < maxVal guard
    for guard in cmd.guards:
        if str(guard.left) == "x":
            # Should be x < 3 (from override)
            from zkterm_tool import encode_transition
            enc = encode_transition(cmd)
            # The guard constraint will be encoded in the matrices
            # Just verify the encoding works (implicitly uses the overridden value)
            assert enc.A.shape[1] == 2  # [x, x']
            break

    # Verify the ranking function also uses the override
    rank = result2.ranking_functions["q0"]
    case = rank.cases[0]
    # The guard should be x <= 3 and expression should be 3 - x
    from zkterm_tool import encode_ranking_functions
    rank_encs = encode_ranking_functions(result2.ranking_functions)
    enc_q0 = rank_encs["q0"]
    assert enc_q0.finite_cases[0].u_j == 3  # Constant term in maxVal - x
