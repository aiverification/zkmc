"""Parser for guarded commands, ranking functions, init conditions, and automaton transitions using Lark."""

from dataclasses import dataclass
from pathlib import Path
from lark import Lark, Transformer, v_args

from .ast_types import (
    GuardedCommand, Comparison, Assignment, CompOp,
    Var, Num, BinOp, Neg, Expr
)
from .ranking_types import RankingCase, RankingFunction
from .automaton_types import AutomatonTransition


# Load grammar from file
GRAMMAR_PATH = Path(__file__).parent / "grammar.lark"


@dataclass
class ParseResult:
    """Result of parsing, including all program components."""
    constants: dict[str, int]
    init_condition: list[Comparison] | None          # Initial condition guard
    commands: list[GuardedCommand]                   # Program transitions
    ranking_functions: dict[str, RankingFunction]    # state -> RankingFunction
    automaton_transitions: list[AutomatonTransition] # Büchi automaton transitions


class ASTTransformer(Transformer):
    """Transform Lark parse tree into our AST types.

    Constants are collected first, then substituted in expressions.
    Handles guarded commands, ranking functions, init conditions, and automaton transitions.
    """

    def __init__(self):
        super().__init__()
        self.constants: dict[str, int] = {}
        self._init_guards: list[Comparison] | None = None
        self.ranking_functions: dict[str, RankingFunction] = {}
        self.automaton_transitions: list[AutomatonTransition] = []

    def start(self, items: list) -> ParseResult:
        # Items are a mix of None (from const_def, ranking_function, automaton_trans, init_condition)
        # and GuardedCommands
        commands = [item for item in items if isinstance(item, GuardedCommand)]
        # ranking_functions, init_condition, and automaton_transitions are collected in their respective fields
        return ParseResult(
            constants=self.constants,
            init_condition=self._init_guards,
            commands=commands,
            ranking_functions=self.ranking_functions,
            automaton_transitions=self.automaton_transitions
        )

    def init_condition(self, items: list) -> None:
        """Parse initial condition: init: guard"""
        guard_comparisons = items[0]  # list of comparisons from guard
        self._init_guards = guard_comparisons
        return None  # Don't include in items list, stored in self._init_guards

    def const_def(self, items: list) -> None:
        name, value = items
        self.constants[str(name)] = int(value)
        return None
    
    def guarded_command(self, items: list) -> GuardedCommand:
        guard_comparisons = items[0]  # list of comparisons from guard
        assignments = items[1]  # list of assignments
        return GuardedCommand(guards=guard_comparisons, assignments=assignments)
    
    def guard(self, items: list) -> list[Comparison]:
        # items will be either a list with "true" token, or list of Comparison objects
        # Lark may pass the keyword as a Token object
        if items and hasattr(items[0], 'type'):
            # It's a Token object - check if it's "true"
            if items[0].value == "true":
                return []  # Empty guard list = always true
        elif items and isinstance(items[0], str) and items[0] == "true":
            # It's a string "true"
            return []
        return list(items)
    
    def comparison(self, items: list) -> Comparison:
        left, op_token, right = items
        op_str = str(op_token)
        # Normalize operators
        if op_str in ("≤", "<="):
            op = CompOp.LE
        elif op_str == "<":
            op = CompOp.LT
        elif op_str in ("=", "=="):
            op = CompOp.EQ
        elif op_str in ("≥", ">="):
            op = CompOp.GE
        elif op_str == ">":
            op = CompOp.GT
        else:
            raise ValueError(f"Unknown comparison operator: {op_str}")
        return Comparison(left=left, right=right, op=op)
    
    def assignments(self, items: list) -> list[Assignment]:
        return list(items)
    
    def assignment(self, items: list) -> Assignment:
        var_token, expr = items
        return Assignment(var=str(var_token), expr=expr)

    def ranking_function(self, items: list) -> None:
        """Parse ranking function: rank(state): cases..."""
        state_token = items[0]
        cases = items[1:]  # list of RankingCase
        state = str(state_token)
        rf = RankingFunction(state=state, cases=cases)
        self.ranking_functions[state] = rf
        return None  # Don't include in items list, stored in self.ranking_functions

    def ranking_case(self, items: list) -> RankingCase:
        """Parse ranking case: [] guard -> expression"""
        guard_comparisons = items[0]  # list of comparisons from guard
        expression = items[1]  # Expr
        return RankingCase(guards=guard_comparisons, expression=expression)

    def automaton_trans(self, items: list) -> None:
        """Parse automaton transition: trans(q, q'): guard or trans!(q, q'): guard"""
        # Items structure depends on whether fair_mark is present
        # If fair mark present: [fair_mark, from_state, to_state, guard]
        # If no fair mark: [from_state, to_state, guard]

        if len(items) == 4:  # Has fair mark
            # fair_mark is items[0], but we just check its presence
            from_state = str(items[1])
            to_state = str(items[2])
            guard_comparisons = items[3]
            is_fair = True
        else:  # No fair mark
            from_state = str(items[0])
            to_state = str(items[1])
            guard_comparisons = items[2]
            is_fair = False

        trans = AutomatonTransition(
            from_state=from_state,
            to_state=to_state,
            guards=guard_comparisons,
            is_fair=is_fair
        )
        self.automaton_transitions.append(trans)
        return None  # Don't include in items list, stored in self.automaton_transitions

    @v_args(inline=True)
    def add(self, left: Expr, right: Expr) -> BinOp:
        return BinOp(op="+", left=left, right=right)
    
    @v_args(inline=True)
    def sub(self, left: Expr, right: Expr) -> BinOp:
        return BinOp(op="-", left=left, right=right)
    
    @v_args(inline=True)
    def mul(self, left: Expr, right: Expr) -> BinOp:
        return BinOp(op="*", left=left, right=right)
    
    @v_args(inline=True)
    def neg(self, expr: Expr) -> Neg:
        return Neg(expr=expr)
    
    @v_args(inline=True)
    def number(self, token) -> Num:
        return Num(value=int(token))
    
    @v_args(inline=True)
    def var(self, token) -> Var:
        name = str(token)
        # Substitute constants immediately
        if name in self.constants:
            return Num(value=self.constants[name])
        return Var(name=name)


def create_parser() -> Lark:
    """Create a Lark parser for all zkterm constructs."""
    grammar = GRAMMAR_PATH.read_text()
    return Lark(grammar, parser="lalr")


def parse(text: str) -> list[GuardedCommand]:
    """Parse text into AST, returning only guarded commands.

    Note: This function ignores init conditions, ranking functions, and automaton transitions.
    Use parse_with_constants() to access all components.
    """
    parser = create_parser()
    tree = parser.parse(text)
    transformer = ASTTransformer()
    result = transformer.transform(tree)
    return result.commands


def parse_with_constants(text: str) -> ParseResult:
    """Parse text into AST, returning all components.

    Returns ParseResult with:
    - constants: Named constants
    - init_condition: Initial condition guard (if present)
    - commands: Guarded commands (program transitions)
    - ranking_functions: Ranking functions by state
    - automaton_transitions: Büchi automaton transitions
    """
    parser = create_parser()
    tree = parser.parse(text)
    transformer = ASTTransformer()
    return transformer.transform(tree)
