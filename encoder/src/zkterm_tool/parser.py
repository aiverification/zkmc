"""Parser for guarded commands using Lark."""

from dataclasses import dataclass
from pathlib import Path
from lark import Lark, Transformer, v_args

from .ast_types import (
    GuardedCommand, Comparison, Assignment, CompOp,
    Var, Num, BinOp, Neg, Expr
)


# Load grammar from file
GRAMMAR_PATH = Path(__file__).parent / "grammar.lark"


@dataclass
class ParseResult:
    """Result of parsing, including constants and commands."""
    constants: dict[str, int]
    commands: list[GuardedCommand]


class ASTTransformer(Transformer):
    """Transform Lark parse tree into our AST types.
    
    Constants are collected first, then substituted in expressions.
    """
    
    def __init__(self):
        super().__init__()
        self.constants: dict[str, int] = {}
    
    def start(self, items: list) -> ParseResult:
        # Items are a mix of None (from const_def) and GuardedCommands
        commands = [item for item in items if isinstance(item, GuardedCommand)]
        return ParseResult(constants=self.constants, commands=commands)
    
    def const_def(self, items: list) -> None:
        name, value = items
        self.constants[str(name)] = int(value)
        return None
    
    def guarded_command(self, items: list) -> GuardedCommand:
        guard_comparisons = items[0]  # list of comparisons from guard
        assignments = items[1]  # list of assignments
        return GuardedCommand(guards=guard_comparisons, assignments=assignments)
    
    def guard(self, items: list) -> list[Comparison]:
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
    """Create a Lark parser for guarded commands."""
    grammar = GRAMMAR_PATH.read_text()
    return Lark(grammar, parser="lalr")


def parse(text: str) -> list[GuardedCommand]:
    """Parse guarded commands text into AST."""
    parser = create_parser()
    tree = parser.parse(text)
    transformer = ASTTransformer()
    result = transformer.transform(tree)
    return result.commands


def parse_with_constants(text: str) -> ParseResult:
    """Parse guarded commands text into AST, returning constants too."""
    parser = create_parser()
    tree = parser.parse(text)
    transformer = ASTTransformer()
    return transformer.transform(tree)
